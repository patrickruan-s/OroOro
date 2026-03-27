import datetime
import json
import secrets
from collections import Counter

import spotipy
from django.contrib.auth.models import User
from django.conf import settings
from django.shortcuts import redirect, render
from django.utils import timezone

from .models import RecentlyPlayedEntry, SpotifyProfile, TopTrackSnapshot, UserListeningSummary
from .spotify_client import get_spotify_oauth, get_top_tracks, sync_user_data


def login_view(request):
    return render(request, "spotify/login.html")


def spotify_login(request):
    state = secrets.token_urlsafe(16)
    request.session["oauth_state"] = state
    sp_oauth = get_spotify_oauth()
    auth_url = sp_oauth.get_authorize_url(state=state)
    return redirect(auth_url)


def spotify_callback(request):
    code = request.GET.get("code")
    returned_state = request.GET.get("state")
    expected_state = request.session.get("oauth_state")
    if not code or returned_state != expected_state:
        return redirect("login")
    request.session.pop("oauth_state", None)
    sp_oauth = get_spotify_oauth()
    token_info = sp_oauth.get_access_token(code, as_dict=True)
    request.session["spotify_token"] = token_info
    return redirect("home")


def home(request):
    token_info = request.session.get("spotify_token")
    if not token_info:
        return redirect("login")

    sp_oauth = get_spotify_oauth()
    if sp_oauth.is_token_expired(token_info):
        token_info = sp_oauth.refresh_access_token(token_info["refresh_token"])
        request.session["spotify_token"] = token_info

    sp = spotipy.Spotify(auth=token_info["access_token"])

    # Resolve or create a Django User + SpotifyProfile for this Spotify session.
    # Call sp.current_user() at most once per request.
    django_user_id = request.session.get("django_user_id")
    django_user = None
    if django_user_id:
        try:
            django_user = User.objects.get(pk=django_user_id)
        except User.DoesNotExist:
            pass

    if django_user is None:
        spotify_user_info = sp.current_user()
        spotify_id = spotify_user_info["id"]
        display_name = spotify_user_info.get("display_name") or ""

        django_user, _ = User.objects.get_or_create(
            username=spotify_id,
            defaults={"first_name": display_name},
        )
        request.session["django_user_id"] = django_user.pk

        expires_at = token_info.get("expires_at")
        token_expires_at = (
            datetime.datetime.fromtimestamp(expires_at, tz=datetime.timezone.utc)
            if expires_at
            else timezone.now() + datetime.timedelta(hours=1)
        )
        SpotifyProfile.objects.get_or_create(
            spotify_id=spotify_id,
            defaults={
                "user": django_user,
                "display_name": display_name,
                "access_token": token_info["access_token"],
                "refresh_token": token_info.get("refresh_token", ""),
                "token_expires_at": token_expires_at,
            },
        )

    profile = SpotifyProfile.objects.filter(user=django_user).first()
    display_name = profile.display_name if profile else ""

    # Decide whether to sync
    one_hour_ago = timezone.now() - datetime.timedelta(hours=1)
    if profile.last_synced_at is None or profile.last_synced_at < one_hour_ago:
        sync_user_data(sp, django_user)
        profile.refresh_from_db()

    # Build context: top 20 long_term tracks ordered by rank
    long_term_snapshots = (
        TopTrackSnapshot.objects.filter(user=django_user, time_range="long_term")
        .select_related("track")
        .prefetch_related("track__artists")
        .order_by("rank")[:20]
    )
    tracks = []
    for snapshot in long_term_snapshots:
        t = snapshot.track
        tracks.append({
            "rank": snapshot.rank,
            "name": t.name,
            "artist": ", ".join(a.name for a in t.artists.all()),
            "album": t.album_name,
            "album_image_url": t.album_image_url,
            "duration_ms": t.duration_ms,
            "explicit": t.explicit,
        })

    # Top 10 long_term artists derived from track.artists
    artist_counts = Counter()
    artist_info = {}
    for snapshot in TopTrackSnapshot.objects.filter(
        user=django_user, time_range="long_term"
    ).select_related("track").prefetch_related("track__artists"):
        for artist in snapshot.track.artists.all():
            artist_counts[artist.spotify_id] += 1
            if artist.spotify_id not in artist_info:
                artist_info[artist.spotify_id] = artist

    top_artist_ids = [aid for aid, _ in artist_counts.most_common(10)]
    top_artists = [artist_info[aid] for aid in top_artist_ids if aid in artist_info]

    # Latest UserListeningSummary for long_term
    summary = (
        UserListeningSummary.objects.filter(user=django_user, time_range="long_term")
        .order_by("-snapshot_date")
        .first()
    )

    # Last 20 recently played entries ordered by played_at desc
    recently_played = (
        RecentlyPlayedEntry.objects.filter(user=django_user)
        .select_related("track")
        .order_by("-played_at")[:20]
    )

    # Top 10 genres from top artists
    genre_counter = Counter()
    for artist in top_artists:
        for genre in artist.genres:
            genre_counter[genre] += 1
    genres = [{"genre": g, "count": c} for g, c in genre_counter.most_common(10)]

    return render(request, "spotify/home.html", {
        "tracks": tracks,
        "top_artists": top_artists,
        "summary": summary,
        "recently_played": recently_played,
        "genres": genres,
        "display_name": display_name,
    })


def logout_view(request):
    request.session.flush()
    return redirect("login")
