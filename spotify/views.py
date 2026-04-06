import datetime
import json
import secrets
import threading
from collections import Counter

import spotipy
from django.contrib.auth.models import User
from django.conf import settings
from django.http import JsonResponse
from django.db.models import Max
from django.shortcuts import redirect, render
from django.utils import timezone

from .models import Artist, RecentlyPlayedEntry, SpotifyProfile, TopTrackSnapshot, Track, UserListeningSummary
from .spotify_client import get_spotify_oauth, get_top_tracks, sync_user_data


VALID_TIME_RANGES = ["short_term", "medium_term", "long_term"]


def _get_personality(avg_valence, avg_energy):
    """Return personality dict based on valence and energy quadrant."""
    if avg_valence is None or avg_energy is None:
        return None
    if avg_valence >= 0.5 and avg_energy >= 0.5:
        return {
            "label": "Party Animal",
            "color": "#1DB954",
            "description": "High energy, high happiness. You live for the moment.",
        }
    elif avg_valence >= 0.5 and avg_energy < 0.5:
        return {
            "label": "Laid-back Optimist",
            "color": "#4A90D9",
            "description": "Positive vibes, relaxed tempo. You find joy in the quiet.",
        }
    elif avg_valence < 0.5 and avg_energy >= 0.5:
        return {
            "label": "Intense Brooder",
            "color": "#E74C3C",
            "description": "High drive, darker themes. You channel emotion into energy.",
        }
    else:
        return {
            "label": "Melancholy Dreamer",
            "color": "#9B59B6",
            "description": "Introspective and atmospheric. You feel everything deeply.",
        }


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

    # Kick off sync in background if stale — render home immediately with current DB data
    one_hour_ago = timezone.now() - datetime.timedelta(hours=1)
    syncing = False
    if profile.last_synced_at is None or profile.last_synced_at < one_hour_ago:
        syncing = True
        SpotifyProfile.objects.filter(user=django_user).update(last_synced_at=timezone.now())
        sp_bg = spotipy.Spotify(auth=token_info["access_token"])
        threading.Thread(target=sync_user_data, args=(sp_bg, django_user), daemon=True).start()

    # Validate time_range query param
    time_range = request.GET.get("range", "long_term")
    if time_range not in VALID_TIME_RANGES:
        time_range = "long_term"

    # Build context: top 20 tracks for the selected time_range ordered by rank
    latest_date = TopTrackSnapshot.objects.filter(
        user=django_user, time_range=time_range
    ).aggregate(Max("snapshot_date"))["snapshot_date__max"]

    snapshots = (
        TopTrackSnapshot.objects.filter(
            user=django_user, time_range=time_range, snapshot_date=latest_date
        )
        .select_related("track")
        .prefetch_related("track__artists")
        .order_by("rank")[:20]
    )
    tracks = []
    for snapshot in snapshots:
        t = snapshot.track
        tracks.append({
            "rank": snapshot.rank,
            "name": t.name,
            "artist": ", ".join(a.name for a in t.artists.all()),
            "album": t.album_name,
            "album_image_url": t.album_image_url,
            "duration_ms": t.duration_ms,
            "explicit": t.explicit,
            "spotify_id": t.spotify_id,
            "artist_ids": [a.spotify_id for a in t.artists.all()],
        })

    # Top 10 artists for the selected time_range derived from track.artists
    artist_counts = Counter()
    artist_info = {}
    for snapshot in TopTrackSnapshot.objects.filter(
        user=django_user, time_range=time_range
    ).select_related("track").prefetch_related("track__artists"):
        for artist in snapshot.track.artists.all():
            artist_counts[artist.spotify_id] += 1
            if artist.spotify_id not in artist_info:
                artist_info[artist.spotify_id] = artist

    top_artist_ids = [aid for aid, _ in artist_counts.most_common(10)]
    top_artists = [artist_info[aid] for aid in top_artist_ids if aid in artist_info]

    # Latest UserListeningSummary for the selected time_range
    summary = (
        UserListeningSummary.objects.filter(user=django_user, time_range=time_range)
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

    # Compute personality from summary
    if summary is not None:
        personality = _get_personality(summary.avg_valence, summary.avg_energy)
    else:
        personality = None

    return render(request, "spotify/home.html", {
        "tracks": tracks,
        "top_artists": top_artists,
        "summary": summary,
        "recently_played": recently_played,
        "genres": genres,
        "display_name": display_name,
        "personality": personality,
        "time_range": time_range,
        "syncing": syncing,
    })


def loading(request):
    token_info = request.session.get("spotify_token")
    if not token_info:
        return redirect("login")
    return render(request, "spotify/loading.html")


def start_sync(request):
    token_info = request.session.get("sync_token") or request.session.get("spotify_token")
    user_id = request.session.get("sync_user_id") or request.session.get("django_user_id")
    if not token_info or not user_id:
        return JsonResponse({"error": "no session"}, status=401)

    try:
        django_user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return JsonResponse({"error": "user not found"}, status=404)

    sp = spotipy.Spotify(auth=token_info["access_token"])

    def run_sync():
        try:
            sync_user_data(sp, django_user)
        except Exception as e:
            request.session["sync_error"] = str(e)
            request.session.save()

    thread = threading.Thread(target=run_sync, daemon=True)
    thread.start()
    return JsonResponse({"status": "started"})


def sync_status(request):
    user_id = request.session.get("sync_user_id") or request.session.get("django_user_id")
    if not user_id:
        return JsonResponse({"done": False, "error": "no session"})

    error = request.session.pop("sync_error", None)
    if error:
        request.session.save()
        return JsonResponse({"done": False, "error": error})

    try:
        django_user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return JsonResponse({"done": False, "error": "user not found"})

    profile = SpotifyProfile.objects.filter(user=django_user).first()
    one_hour_ago = timezone.now() - datetime.timedelta(hours=1)
    done = profile is not None and profile.last_synced_at is not None and profile.last_synced_at >= one_hour_ago
    return JsonResponse({"done": done})


def profile(request):
    token_info = request.session.get("spotify_token")
    if not token_info:
        return redirect("login")

    django_user_id = request.session.get("django_user_id")
    django_user = None
    if django_user_id:
        try:
            django_user = User.objects.get(pk=django_user_id)
        except User.DoesNotExist:
            pass
    if django_user is None:
        return redirect("login")

    prof = SpotifyProfile.objects.filter(user=django_user).first()
    display_name = prof.display_name if prof else django_user.username

    summary = (
        UserListeningSummary.objects
        .filter(user=django_user, time_range="long_term")
        .order_by("-snapshot_date")
        .first()
    )
    personality = _get_personality(summary.avg_valence, summary.avg_energy) if summary else None

    artist_counts = Counter()
    artist_info = {}
    for snapshot in (
        TopTrackSnapshot.objects.filter(user=django_user)
        .select_related("track").prefetch_related("track__artists")
    ):
        for artist in snapshot.track.artists.all():
            artist_counts[artist.spotify_id] += 1
            if artist.spotify_id not in artist_info:
                artist_info[artist.spotify_id] = artist

    top_artist_ids = [aid for aid, _ in artist_counts.most_common(8)]
    top_artists = [artist_info[aid] for aid in top_artist_ids if aid in artist_info]

    return render(request, "spotify/profile.html", {
        "display_name": display_name,
        "summary": summary,
        "personality": personality,
        "top_artists": top_artists,
    })


def artist_detail(request, spotify_id):
    token_info = request.session.get("spotify_token")
    if not token_info:
        return redirect("login")

    django_user_id = request.session.get("django_user_id")
    django_user = None
    if django_user_id:
        try:
            django_user = User.objects.get(pk=django_user_id)
        except User.DoesNotExist:
            pass
    if django_user is None:
        return redirect("login")

    try:
        artist = Artist.objects.get(spotify_id=spotify_id)
    except Artist.DoesNotExist:
        from django.http import Http404
        raise Http404

    tracks = Track.objects.filter(
        artists__spotify_id=spotify_id,
        toptracksnapshot__user=django_user,
    ).distinct().prefetch_related("artists")

    # Compute per-artist audio feature averages (skip None values)
    energy_vals = [t.energy for t in tracks if t.energy is not None]
    valence_vals = [t.valence for t in tracks if t.valence is not None]
    dance_vals = [t.danceability for t in tracks if t.danceability is not None]
    acoustic_vals = [t.acousticness for t in tracks if t.acousticness is not None]
    popularity_vals = [t.popularity for t in tracks]

    avg_energy = sum(energy_vals) / len(energy_vals) if energy_vals else None
    avg_valence = sum(valence_vals) / len(valence_vals) if valence_vals else None
    avg_danceability = sum(dance_vals) / len(dance_vals) if dance_vals else None
    avg_acousticness = sum(acoustic_vals) / len(acoustic_vals) if acoustic_vals else None

    obscurity_score = (100 - sum(popularity_vals) / len(popularity_vals)) if popularity_vals else None
    if obscurity_score is not None:
        if obscurity_score >= 70:
            undergroundness_label = "Deep Underground"
        elif obscurity_score >= 40:
            undergroundness_label = "Indie"
        else:
            undergroundness_label = "Mainstream"
    else:
        undergroundness_label = None

    play_count = RecentlyPlayedEntry.objects.filter(
        user=django_user,
        track__artists__spotify_id=spotify_id,
    ).count()

    mood_label_dict = _get_personality(avg_valence, avg_energy)
    mood_label = mood_label_dict["label"] if mood_label_dict else None

    track_list = []
    for t in sorted(tracks, key=lambda x: x.popularity, reverse=True):
        track_list.append({
            "name": t.name,
            "album_name": t.album_name,
            "album_image_url": t.album_image_url,
            "duration_ms": t.duration_ms,
            "popularity": t.popularity,
            "energy": t.energy,
            "valence": t.valence,
            "danceability": t.danceability,
            "spotify_id": t.spotify_id,
        })

    return render(request, "spotify/artist_detail.html", {
        "artist": artist,
        "track_list": track_list,
        "avg_energy": avg_energy,
        "avg_valence": avg_valence,
        "avg_danceability": avg_danceability,
        "avg_acousticness": avg_acousticness,
        "obscurity_score": obscurity_score,
        "undergroundness_label": undergroundness_label,
        "play_count": play_count,
        "mood_label": mood_label,
    })


def heatmap_data(request):
    token_info = request.session.get("spotify_token")
    if not token_info:
        return JsonResponse({"error": "unauthenticated"}, status=401)

    django_user_id = request.session.get("django_user_id")
    django_user = None
    if django_user_id:
        try:
            django_user = User.objects.get(pk=django_user_id)
        except User.DoesNotExist:
            pass
    if django_user is None:
        return JsonResponse({"error": "unauthenticated"}, status=401)

    entries = RecentlyPlayedEntry.objects.filter(user=django_user).values("played_at")

    date_counts = Counter()
    for entry in entries:
        date_counts[entry["played_at"].date()] += 1

    data = [
        {"date": str(date), "count": count}
        for date, count in sorted(date_counts.items())
    ]

    return JsonResponse({"data": data})


def logout_view(request):
    request.session.flush()
    return redirect("login")
