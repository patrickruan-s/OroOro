from django.contrib.auth.models import User
from django.db import models


class SpotifyProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    spotify_id = models.CharField(max_length=100, unique=True)
    display_name = models.CharField(max_length=200, blank=True)
    access_token = models.TextField()
    refresh_token = models.TextField()
    token_expires_at = models.DateTimeField()
    last_synced_at = models.DateTimeField(null=True, blank=True)


class Artist(models.Model):
    spotify_id = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=200)
    genres = models.JSONField(default=list)
    image_url = models.URLField(blank=True)


class Track(models.Model):
    spotify_id = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=200)
    artists = models.ManyToManyField(Artist)
    album_name = models.CharField(max_length=200)
    album_image_url = models.URLField(blank=True)
    release_date = models.DateField(null=True, blank=True)
    duration_ms = models.IntegerField()
    popularity = models.IntegerField()
    explicit = models.BooleanField(default=False)
    energy = models.FloatField(null=True, blank=True)
    valence = models.FloatField(null=True, blank=True)
    danceability = models.FloatField(null=True, blank=True)
    acousticness = models.FloatField(null=True, blank=True)
    tempo = models.FloatField(null=True, blank=True)


class TopTrackSnapshot(models.Model):
    TIME_RANGES = [
        ("short_term", "Last 4 Weeks"),
        ("medium_term", "Last 6 Months"),
        ("long_term", "All Time"),
    ]
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    track = models.ForeignKey(Track, on_delete=models.CASCADE)
    time_range = models.CharField(max_length=20, choices=TIME_RANGES)
    rank = models.IntegerField()
    snapshot_date = models.DateField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "track", "time_range", "snapshot_date")
        indexes = [models.Index(fields=["user", "time_range"])]


class RecentlyPlayedEntry(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    track = models.ForeignKey(Track, on_delete=models.CASCADE)
    played_at = models.DateTimeField(db_index=True)

    class Meta:
        unique_together = ("user", "track", "played_at")
        indexes = [models.Index(fields=["user", "played_at"])]


class UserListeningSummary(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    snapshot_date = models.DateField(auto_now_add=True)
    time_range = models.CharField(max_length=20)
    avg_energy = models.FloatField(null=True)
    avg_valence = models.FloatField(null=True)
    avg_danceability = models.FloatField(null=True)
    top_genre = models.CharField(max_length=100, blank=True)
    total_minutes = models.IntegerField(default=0)
    obscurity_score = models.FloatField(null=True)

    class Meta:
        unique_together = ("user", "time_range", "snapshot_date")
