import datetime
import time
from collections import Counter

import requests
import spotipy
import spotipy.oauth2
import spotipy.cache_handler
from django.utils import timezone

from .models import Artist, RecentlyPlayedEntry, SpotifyProfile, TopTrackSnapshot, Track, UserListeningSummary

SCOPE = "user-library-read user-top-read user-read-recently-played"


def get_spotify_oauth():
    return spotipy.oauth2.SpotifyOAuth(
        scope=SCOPE,
        cache_handler=spotipy.cache_handler.MemoryCacheHandler(),
        show_dialog=False,
    )


def get_top_tracks(sp, limit=20, time_range="long_term") -> list:
    results = sp.current_user_top_tracks(limit=limit, time_range=time_range)
    tracks = []
    for i, item in enumerate(results["items"]):
        tracks.append({
            "rank": i + 1,
            "name": item["name"],
            "artist": ", ".join(a["name"] for a in item["artists"]),
            "album": item["album"]["name"],
            "duration_ms": item["duration_ms"],
            "explicit": item["explicit"],
            "id": item["id"],
        })
    return tracks


def get_top_artists(sp, limit=20, time_range="long_term") -> list:
    results = sp.current_user_top_artists(limit=limit, time_range=time_range)
    artists = []
    for i, item in enumerate(results["items"]):
        image_url = item["images"][0]["url"] if item.get("images") else ""
        artists.append({
            "rank": i + 1,
            "spotify_id": item["id"],
            "name": item["name"],
            "genres": item.get("genres", []),
            "image_url": image_url,
        })
    return artists


def get_recently_played(sp, limit=50) -> list:
    results = sp.current_user_recently_played(limit=limit)
    entries = []
    for item in results["items"]:
        track = item["track"]
        album = track["album"]
        album_image_url = album["images"][0]["url"] if album.get("images") else ""
        entries.append({
            "played_at": item["played_at"],
            "spotify_id": track["id"],
            "name": track["name"],
            "artist": ", ".join(a["name"] for a in track["artists"]),
            "album_name": album["name"],
            "album_image_url": album_image_url,
            "duration_ms": track["duration_ms"],
            "popularity": track.get("popularity", 0),
            "explicit": track.get("explicit", False),
        })
    return entries


# Genre-based audio feature estimates used as fallback when the
# audio-features API endpoint is unavailable (deprecated for apps after Nov 2024).
# Values are heuristic approximations on a 0.0–1.0 scale.
_GENRE_FEATURE_MAP = {
    "dance":       {"energy": 0.80, "valence": 0.65, "danceability": 0.85, "acousticness": 0.05},
    "electronic":  {"energy": 0.80, "valence": 0.43, "danceability": 0.80, "acousticness": 0.05},
    "edm":         {"energy": 0.85, "valence": 0.60, "danceability": 0.85, "acousticness": 0.04},
    "pop":         {"energy": 0.60, "valence": 0.65, "danceability": 0.70, "acousticness": 0.15},
    "hip hop":     {"energy": 0.65, "valence": 0.42, "danceability": 0.78, "acousticness": 0.10},
    "rap":         {"energy": 0.65, "valence": 0.48, "danceability": 0.78, "acousticness": 0.08},
    "r&b":         {"energy": 0.44, "valence": 0.58, "danceability": 0.72, "acousticness": 0.20},
    "soul":        {"energy": 0.42, "valence": 0.60, "danceability": 0.68, "acousticness": 0.30},
    "rock":        {"energy": 0.78, "valence": 0.45, "danceability": 0.50, "acousticness": 0.10},
    "metal":       {"energy": 0.92, "valence": 0.28, "danceability": 0.35, "acousticness": 0.05},
    "punk":        {"energy": 0.88, "valence": 0.40, "danceability": 0.45, "acousticness": 0.05},
    "indie":       {"energy": 0.46, "valence": 0.44, "danceability": 0.55, "acousticness": 0.28},
    "alternative": {"energy": 0.60, "valence": 0.45, "danceability": 0.52, "acousticness": 0.18},
    "folk":        {"energy": 0.35, "valence": 0.55, "danceability": 0.42, "acousticness": 0.80},
    "acoustic":    {"energy": 0.32, "valence": 0.55, "danceability": 0.40, "acousticness": 0.85},
    "classical":   {"energy": 0.25, "valence": 0.50, "danceability": 0.22, "acousticness": 0.92},
    "jazz":        {"energy": 0.40, "valence": 0.60, "danceability": 0.55, "acousticness": 0.72},
    "blues":       {"energy": 0.45, "valence": 0.42, "danceability": 0.50, "acousticness": 0.55},
    "country":     {"energy": 0.52, "valence": 0.62, "danceability": 0.55, "acousticness": 0.50},
    "latin":       {"energy": 0.70, "valence": 0.72, "danceability": 0.82, "acousticness": 0.15},
    "reggae":      {"energy": 0.55, "valence": 0.70, "danceability": 0.75, "acousticness": 0.25},
    "ambient":     {"energy": 0.20, "valence": 0.40, "danceability": 0.25, "acousticness": 0.60},
}
_DEFAULT_FEATURES = {"energy": 0.55, "valence": 0.50, "danceability": 0.55, "acousticness": 0.30}


def _estimate_features_from_genres(genres: list) -> dict:
    """
    Derive estimated audio features from a list of genre strings.
    Averages values across all matched genre keywords found in the list.
    Falls back to neutral defaults if no genres match.
    """
    breakpoint()
    matched = []
    for genre in genres:
        genre_lower = genre.lower()
        for keyword, features in _GENRE_FEATURE_MAP.items():
            if keyword in genre_lower:
                matched.append(features)
                break
    if not matched:
        return dict(_DEFAULT_FEATURES)
    result = {}
    for key in ("energy", "valence", "danceability", "acousticness"):
        result[key] = sum(f[key] for f in matched) / len(matched)
    return result


def get_audio_features(sp, track_ids: list) -> dict:
    """
    Returns audio features keyed by track spotify_id.
    Falls back to genre-based estimation if the endpoint is unavailable
    (deprecated for apps created after Nov 2024 — returns 403).
    """
    features_map = {}
    api_unavailable = False

    # Spotify API allows up to 100 ids per request
    for i in range(0, len(track_ids), 100):
        batch = track_ids[i:i + 100]
        try:
            results = sp.audio_features(batch)
        except spotipy.SpotifyException as e:
            if e.http_status == 403:
                api_unavailable = True
                break
            raise
        if not results:
            continue
        for feat in results:
            if feat is None:
                continue
            features_map[feat["id"]] = {
                "energy": feat.get("energy"),
                "valence": feat.get("valence"),
                "danceability": feat.get("danceability"),
                "acousticness": feat.get("acousticness"),
                "tempo": feat.get("tempo"),
            }

    if api_unavailable:
        # Estimate features from genres for each track via their artists
        for track_obj in Track.objects.filter(spotify_id__in=track_ids).prefetch_related("artists"):
            genres = []
            for artist in track_obj.artists.all():
                breakpoint()
                genres.extend(artist.genres)
            estimated = _estimate_features_from_genres(genres)
            features_map[track_obj.spotify_id] = {**estimated, "tempo": None}

    return features_map


def _fetch_genres_from_musicbrainz(artist_name: str) -> list:
    """Look up genre tags for an artist from MusicBrainz."""
    try:
        resp = requests.get(
            "https://musicbrainz.org/ws/2/artist/",
            params={"query": f'artist:"{artist_name}"', "fmt": "json", "limit": 1},
            headers={"User-Agent": "SpotifyAnalysisApp/1.0 (spotify-analysis)"},
            timeout=5,
        )
        resp.raise_for_status()
        artists = resp.json().get("artists", [])
        if not artists:
            return []
        tags = artists[0].get("tags", [])
        tags_sorted = sorted(tags, key=lambda t: t.get("count", 0), reverse=True)
        return [t["name"] for t in tags_sorted[:8]]
    except Exception:
        return []


def _parse_release_date(date_str):
    if not date_str:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            return datetime.datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    return None


def sync_user_data(sp, user):
    all_top_track_ids = []

    # Step 1: For each time_range, fetch top artists (with genres) and top tracks
    for time_range in ["short_term", "medium_term", "long_term"]:
        # Fetch full artist details (genres, image) first so they're available
        # when audio features are estimated from genres later in this sync.
        top_artists_result = sp.current_user_top_artists(limit=50, time_range=time_range)
        for artist_data in top_artists_result["items"]:
            image_url = artist_data["images"][0]["url"] if artist_data.get("images") else ""
            Artist.objects.update_or_create(
                spotify_id=artist_data["id"],
                defaults={
                    "name": artist_data["name"],
                    "genres": artist_data.get("genres", []),
                    "image_url": image_url,
                },
            )

        results = sp.current_user_top_tracks(limit=50, time_range=time_range)
        for i, item in enumerate(results["items"]):
            # Upsert artists — genres already populated above from top_artists call
            artist_objs = []
            for artist_data in item["artists"]:
                artist_obj, _ = Artist.objects.update_or_create(
                    spotify_id=artist_data["id"],
                    defaults={
                        "name": artist_data["name"],
                    },
                )
                artist_objs.append(artist_obj)

            # Upsert track
            album = item["album"]
            album_image_url = album["images"][0]["url"] if album.get("images") else ""
            release_date = _parse_release_date(album.get("release_date", ""))

            track_obj, _ = Track.objects.update_or_create(
                spotify_id=item["id"],
                defaults={
                    "name": item["name"],
                    "album_name": album["name"],
                    "album_image_url": album_image_url,
                    "release_date": release_date,
                    "duration_ms": item["duration_ms"],
                    "popularity": item.get("popularity", 0),
                    "explicit": item.get("explicit", False),
                },
            )
            track_obj.artists.set(artist_objs)

            all_top_track_ids.append(item["id"])

            # Save/update TopTrackSnapshot for today (rank may change between syncs)
            TopTrackSnapshot.objects.update_or_create(
                user=user,
                track=track_obj,
                time_range=time_range,
                snapshot_date=datetime.date.today(),
                defaults={"rank": i + 1},
            )

    # Fetch genres from MusicBrainz once for all artists still missing genres
    artists_needing_genres = list(Artist.objects.filter(genres=[]))
    for i, artist_obj in enumerate(artists_needing_genres):
        if i > 0:
            time.sleep(1)  # MusicBrainz rate limit: 1 req/sec (only between requests)
        genres = _fetch_genres_from_musicbrainz(artist_obj.name)
        if genres:
            artist_obj.genres = genres
            artist_obj.save(update_fields=["genres"])

    # Step 2: Fetch audio features only for tracks synced in this call
    tracks_missing_features = Track.objects.filter(
        spotify_id__in=all_top_track_ids, energy__isnull=True
    )
    missing_ids = list(tracks_missing_features.values_list("spotify_id", flat=True))
    if missing_ids:
        features_map = get_audio_features(sp, missing_ids)
        for track_obj in tracks_missing_features:
            feat = features_map.get(track_obj.spotify_id)
            if feat:
                track_obj.energy = feat.get("energy")
                track_obj.valence = feat.get("valence")
                track_obj.danceability = feat.get("danceability")
                track_obj.acousticness = feat.get("acousticness")
                track_obj.tempo = feat.get("tempo")
                track_obj.save()

    # Step 3: Fetch recently played and upsert RecentlyPlayedEntry records
    recent_entries = get_recently_played(sp, limit=50)
    for entry in recent_entries:
        # Upsert track
        track_obj, _ = Track.objects.update_or_create(
            spotify_id=entry["spotify_id"],
            defaults={
                "name": entry["name"],
                "album_name": entry["album_name"],
                "album_image_url": entry["album_image_url"],
                "duration_ms": entry["duration_ms"],
                "popularity": entry["popularity"],
                "explicit": entry["explicit"],
            },
        )

        played_at = entry["played_at"]
        if isinstance(played_at, str):
            # Parse ISO 8601 string
            played_at = datetime.datetime.fromisoformat(played_at.replace("Z", "+00:00"))

        RecentlyPlayedEntry.objects.get_or_create(
            user=user,
            track=track_obj,
            played_at=played_at,
        )

    # Step 4: Compute and save UserListeningSummary for "long_term"
    long_term_snapshots = TopTrackSnapshot.objects.filter(
        user=user, time_range="long_term"
    ).select_related("track")

    long_term_tracks = [s.track for s in long_term_snapshots]

    energies = [t.energy for t in long_term_tracks if t.energy is not None]
    valences = [t.valence for t in long_term_tracks if t.valence is not None]
    danceabilities = [t.danceability for t in long_term_tracks if t.danceability is not None]
    popularities = [t.popularity for t in long_term_tracks]
    total_ms = sum(t.duration_ms for t in long_term_tracks)

    avg_energy = sum(energies) / len(energies) if energies else None
    avg_valence = sum(valences) / len(valences) if valences else None
    avg_danceability = sum(danceabilities) / len(danceabilities) if danceabilities else None
    total_minutes = int(total_ms / 60000)
    obscurity_score = (100 - sum(popularities) / len(popularities)) if popularities else None

    # Determine top genre from top artists linked to long_term tracks
    genre_counter = Counter()
    for track_obj in long_term_tracks:
        for artist_obj in track_obj.artists.all():
            for genre in artist_obj.genres:
                genre_counter[genre] += 1

    top_genre = genre_counter.most_common(1)[0][0] if genre_counter else ""

    today = timezone.now().date()
    UserListeningSummary.objects.update_or_create(
        user=user,
        time_range="long_term",
        snapshot_date=today,
        defaults={
            "avg_energy": avg_energy,
            "avg_valence": avg_valence,
            "avg_danceability": avg_danceability,
            "top_genre": top_genre,
            "total_minutes": total_minutes,
            "obscurity_score": obscurity_score,
        },
    )

    # Step 5: Update SpotifyProfile.last_synced_at
    SpotifyProfile.objects.filter(user=user).update(last_synced_at=timezone.now())
