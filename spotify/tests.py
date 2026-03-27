import datetime
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from .models import (
    Artist,
    RecentlyPlayedEntry,
    TopTrackSnapshot,
    Track,
    UserListeningSummary,
)
from .spotify_client import (
    get_audio_features,
    get_recently_played,
    get_top_artists,
    get_top_tracks,
)


# ---------------------------------------------------------------------------
# Fake data helpers
# ---------------------------------------------------------------------------

def make_user(username="testuser"):
    return User.objects.create_user(username=username, password="password")


def make_artist(spotify_id="artist001", name="Test Artist", genres=None):
    if genres is None:
        genres = ["pop", "indie"]
    return Artist.objects.create(
        spotify_id=spotify_id,
        name=name,
        genres=genres,
        image_url="https://example.com/artist.jpg",
    )


def make_track(spotify_id="track001", name="Test Track", artist=None):
    track = Track.objects.create(
        spotify_id=spotify_id,
        name=name,
        album_name="Test Album",
        album_image_url="https://example.com/album.jpg",
        duration_ms=210000,
        popularity=75,
        explicit=False,
    )
    if artist is not None:
        track.artists.add(artist)
    return track


# ---------------------------------------------------------------------------
# 1. ArtistModelTest
# ---------------------------------------------------------------------------

class ArtistModelTest(TestCase):

    def test_create_artist(self):
        artist = make_artist(
            spotify_id="a1",
            name="My Artist",
            genres=["rock", "alternative"],
        )
        saved = Artist.objects.get(pk=artist.pk)
        self.assertEqual(saved.spotify_id, "a1")
        self.assertEqual(saved.name, "My Artist")
        self.assertIsInstance(saved.genres, list)
        self.assertEqual(saved.genres, ["rock", "alternative"])
        self.assertEqual(saved.image_url, "https://example.com/artist.jpg")

    def test_artist_spotify_id_unique(self):
        make_artist(spotify_id="dup_artist")
        with self.assertRaises(IntegrityError):
            Artist.objects.create(
                spotify_id="dup_artist",
                name="Duplicate Artist",
                genres=[],
            )


# ---------------------------------------------------------------------------
# 2. TrackModelTest
# ---------------------------------------------------------------------------

class TrackModelTest(TestCase):

    def test_create_track(self):
        artist = make_artist()
        track = make_track(artist=artist)
        saved = Track.objects.get(pk=track.pk)
        self.assertEqual(saved.spotify_id, "track001")
        self.assertEqual(saved.name, "Test Track")
        self.assertIn(artist, saved.artists.all())

    def test_audio_features_nullable(self):
        track = Track.objects.create(
            spotify_id="track_no_feat",
            name="No Features Track",
            album_name="Album",
            duration_ms=180000,
            popularity=50,
            explicit=False,
        )
        saved = Track.objects.get(pk=track.pk)
        self.assertIsNone(saved.energy)
        self.assertIsNone(saved.valence)
        self.assertIsNone(saved.danceability)
        self.assertIsNone(saved.acousticness)
        self.assertIsNone(saved.tempo)

    def test_track_spotify_id_unique(self):
        make_track(spotify_id="dup_track")
        with self.assertRaises(IntegrityError):
            Track.objects.create(
                spotify_id="dup_track",
                name="Duplicate Track",
                album_name="Album",
                duration_ms=180000,
                popularity=50,
                explicit=False,
            )


# ---------------------------------------------------------------------------
# 3. TopTrackSnapshotTest
# ---------------------------------------------------------------------------

class TopTrackSnapshotTest(TestCase):

    def setUp(self):
        self.user = make_user()
        self.track = make_track()

    def test_create_snapshot(self):
        snapshot = TopTrackSnapshot.objects.create(
            user=self.user,
            track=self.track,
            time_range="long_term",
            rank=1,
        )
        saved = TopTrackSnapshot.objects.get(pk=snapshot.pk)
        self.assertEqual(saved.rank, 1)
        self.assertEqual(saved.time_range, "long_term")
        self.assertIsNotNone(saved.snapshot_date)

    def test_unique_together_constraint(self):
        today = timezone.now().date()
        TopTrackSnapshot.objects.create(
            user=self.user,
            track=self.track,
            time_range="short_term",
            rank=1,
        )
        with self.assertRaises(IntegrityError):
            TopTrackSnapshot.objects.create(
                user=self.user,
                track=self.track,
                time_range="short_term",
                rank=2,
            )


# ---------------------------------------------------------------------------
# 4. RecentlyPlayedEntryTest
# ---------------------------------------------------------------------------

class RecentlyPlayedEntryTest(TestCase):

    def setUp(self):
        self.user = make_user()
        self.track = make_track()
        self.played_at = datetime.datetime(2026, 3, 27, 12, 0, 0, tzinfo=datetime.timezone.utc)

    def test_create_entry(self):
        entry = RecentlyPlayedEntry.objects.create(
            user=self.user,
            track=self.track,
            played_at=self.played_at,
        )
        saved = RecentlyPlayedEntry.objects.get(pk=entry.pk)
        self.assertEqual(saved.played_at, self.played_at)
        self.assertEqual(saved.user, self.user)
        self.assertEqual(saved.track, self.track)

    def test_unique_together_prevents_duplicates(self):
        RecentlyPlayedEntry.objects.create(
            user=self.user,
            track=self.track,
            played_at=self.played_at,
        )
        with self.assertRaises(IntegrityError):
            RecentlyPlayedEntry.objects.create(
                user=self.user,
                track=self.track,
                played_at=self.played_at,
            )


# ---------------------------------------------------------------------------
# 5. UserListeningSummaryTest
# ---------------------------------------------------------------------------

class UserListeningSummaryTest(TestCase):

    def test_create_summary(self):
        user = make_user()
        summary = UserListeningSummary.objects.create(
            user=user,
            time_range="long_term",
            avg_energy=0.75,
            avg_valence=0.60,
            avg_danceability=0.80,
            top_genre="pop",
            total_minutes=120,
            obscurity_score=35.5,
        )
        saved = UserListeningSummary.objects.get(pk=summary.pk)
        self.assertEqual(saved.time_range, "long_term")
        self.assertAlmostEqual(saved.avg_energy, 0.75)
        self.assertAlmostEqual(saved.avg_valence, 0.60)
        self.assertAlmostEqual(saved.avg_danceability, 0.80)
        self.assertEqual(saved.top_genre, "pop")
        self.assertEqual(saved.total_minutes, 120)
        self.assertAlmostEqual(saved.obscurity_score, 35.5)


# ---------------------------------------------------------------------------
# 6. GetTopTracksTest
# ---------------------------------------------------------------------------

class GetTopTracksTest(TestCase):

    def _make_fake_track_item(self, track_id, name, artist_name, album_name):
        return {
            "id": track_id,
            "name": name,
            "artists": [{"name": artist_name}],
            "album": {"name": album_name},
            "duration_ms": 200000,
            "explicit": False,
        }

    def test_get_top_tracks_returns_correct_structure(self):
        fake_response = {
            "items": [
                self._make_fake_track_item("t1", "Track One", "Artist A", "Album X"),
                self._make_fake_track_item("t2", "Track Two", "Artist B", "Album Y"),
            ]
        }
        sp = MagicMock()
        sp.current_user_top_tracks.return_value = fake_response

        result = get_top_tracks(sp, limit=2, time_range="long_term")

        sp.current_user_top_tracks.assert_called_once_with(limit=2, time_range="long_term")
        self.assertEqual(len(result), 2)

        expected_keys = {"rank", "name", "artist", "album", "duration_ms", "explicit", "id"}
        for item in result:
            self.assertEqual(set(item.keys()), expected_keys)

        self.assertEqual(result[0]["rank"], 1)
        self.assertEqual(result[0]["name"], "Track One")
        self.assertEqual(result[0]["artist"], "Artist A")
        self.assertEqual(result[0]["album"], "Album X")
        self.assertEqual(result[0]["id"], "t1")
        self.assertEqual(result[0]["duration_ms"], 200000)
        self.assertFalse(result[0]["explicit"])

        self.assertEqual(result[1]["rank"], 2)
        self.assertEqual(result[1]["name"], "Track Two")
        self.assertEqual(result[1]["id"], "t2")


# ---------------------------------------------------------------------------
# 7. GetAudioFeaturesTest
# ---------------------------------------------------------------------------

class GetAudioFeaturesTest(TestCase):

    def test_get_audio_features_skips_none_entries(self):
        fake_features = [
            {
                "id": "id1",
                "energy": 0.85,
                "valence": 0.70,
                "danceability": 0.65,
                "acousticness": 0.10,
                "tempo": 128.0,
            },
            None,  # should be skipped
        ]
        sp = MagicMock()
        sp.audio_features.return_value = fake_features

        result = get_audio_features(sp, ["id1", "id2"])

        self.assertIn("id1", result)
        self.assertNotIn("id2", result)
        self.assertNotIn(None, result)

        self.assertAlmostEqual(result["id1"]["energy"], 0.85)
        self.assertAlmostEqual(result["id1"]["valence"], 0.70)
        self.assertAlmostEqual(result["id1"]["danceability"], 0.65)
        self.assertAlmostEqual(result["id1"]["acousticness"], 0.10)
        self.assertAlmostEqual(result["id1"]["tempo"], 128.0)


# ---------------------------------------------------------------------------
# 8. GetTopArtistsTest
# ---------------------------------------------------------------------------

class GetTopArtistsTest(TestCase):

    def _make_fake_artist_item(self, artist_id, name, genres, image_url):
        return {
            "id": artist_id,
            "name": name,
            "genres": genres,
            "images": [{"url": image_url}],
        }

    def test_get_top_artists_returns_correct_structure(self):
        fake_response = {
            "items": [
                self._make_fake_artist_item("a1", "Artist One", ["pop", "indie"], "https://img1.com/a1.jpg"),
                self._make_fake_artist_item("a2", "Artist Two", ["rock"], "https://img1.com/a2.jpg"),
            ]
        }
        sp = MagicMock()
        sp.current_user_top_artists.return_value = fake_response

        result = get_top_artists(sp, limit=2)

        sp.current_user_top_artists.assert_called_once_with(limit=2, time_range="long_term")
        self.assertEqual(len(result), 2)

        self.assertEqual(result[0]["rank"], 1)
        self.assertEqual(result[0]["name"], "Artist One")
        self.assertEqual(result[0]["genres"], ["pop", "indie"])
        self.assertEqual(result[0]["image_url"], "https://img1.com/a1.jpg")
        self.assertEqual(result[0]["spotify_id"], "a1")

        self.assertEqual(result[1]["rank"], 2)
        self.assertEqual(result[1]["name"], "Artist Two")
        self.assertEqual(result[1]["genres"], ["rock"])
        self.assertEqual(result[1]["image_url"], "https://img1.com/a2.jpg")


# ---------------------------------------------------------------------------
# 9. GetRecentlyPlayedTest
# ---------------------------------------------------------------------------

class GetRecentlyPlayedTest(TestCase):

    def _make_fake_played_item(self, track_id, track_name, artist_name, played_at):
        return {
            "played_at": played_at,
            "track": {
                "id": track_id,
                "name": track_name,
                "artists": [{"name": artist_name}],
                "album": {
                    "name": "Some Album",
                    "images": [{"url": "https://example.com/album.jpg"}],
                },
                "duration_ms": 190000,
                "popularity": 60,
                "explicit": False,
            },
        }

    def test_get_recently_played_returns_correct_structure(self):
        fake_response = {
            "items": [
                self._make_fake_played_item("t1", "Song One", "Artist A", "2026-03-27T10:00:00Z"),
                self._make_fake_played_item("t2", "Song Two", "Artist B", "2026-03-27T11:00:00Z"),
            ]
        }
        sp = MagicMock()
        sp.current_user_recently_played.return_value = fake_response

        result = get_recently_played(sp, limit=2)

        sp.current_user_recently_played.assert_called_once_with(limit=2)
        self.assertEqual(len(result), 2)

        expected_keys = {
            "played_at", "spotify_id", "name", "artist",
            "album_name", "album_image_url", "duration_ms", "popularity", "explicit",
        }
        for item in result:
            self.assertEqual(set(item.keys()), expected_keys)

        self.assertEqual(result[0]["played_at"], "2026-03-27T10:00:00Z")
        self.assertEqual(result[0]["spotify_id"], "t1")
        self.assertEqual(result[0]["name"], "Song One")
        self.assertEqual(result[0]["artist"], "Artist A")
        self.assertEqual(result[0]["album_name"], "Some Album")
        self.assertEqual(result[0]["album_image_url"], "https://example.com/album.jpg")

        self.assertEqual(result[1]["spotify_id"], "t2")
        self.assertEqual(result[1]["name"], "Song Two")


# ---------------------------------------------------------------------------
# 10. HomeViewTest
# ---------------------------------------------------------------------------

class HomeViewTest(TestCase):

    def test_redirects_when_no_token(self):
        response = self.client.get("/")
        self.assertRedirects(response, "/login/")

    @patch("spotify.views.sync_user_data")
    @patch("spotify.views.get_spotify_oauth")
    @patch("spotify.views.spotipy.Spotify")
    def test_home_with_mocked_sync(self, mock_spotify_cls, mock_get_oauth, mock_sync):
        # Set up fake token
        fake_token = {
            "access_token": "fake_access_token",
            "refresh_token": "fake_refresh_token",
            "expires_at": 9999999999,
        }

        # Configure OAuth mock: token is NOT expired
        mock_oauth = MagicMock()
        mock_oauth.is_token_expired.return_value = False
        mock_get_oauth.return_value = mock_oauth

        # Configure Spotify mock: current_user returns a fake user
        mock_sp = MagicMock()
        mock_sp.current_user.return_value = {
            "id": "fake_spotify_user",
            "display_name": "Fake User",
        }
        mock_spotify_cls.return_value = mock_sp

        # sync_user_data does nothing (already patched with MagicMock default)

        # Set session token
        session = self.client.session
        session["spotify_token"] = fake_token
        session.save()

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)


# ---------------------------------------------------------------------------
# 11. LoginLogoutViewTest
# ---------------------------------------------------------------------------

class LoginLogoutViewTest(TestCase):

    def test_login_page_renders(self):
        response = self.client.get("/login/")
        self.assertEqual(response.status_code, 200)

    def test_logout_clears_session(self):
        # Populate the session with some data
        session = self.client.session
        session["spotify_token"] = {"access_token": "abc123"}
        session["django_user_id"] = 42
        session.save()

        response = self.client.get("/logout/")

        # Should redirect to login
        self.assertRedirects(response, "/login/")

        # Session should be empty after flush
        self.assertNotIn("spotify_token", self.client.session)
        self.assertNotIn("django_user_id", self.client.session)
