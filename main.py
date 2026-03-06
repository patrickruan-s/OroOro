import numpy as np # linear algebra
import pandas as pd # data processing, CSV file I/O (e.g. pd.read_csv)
import matplotlib.pyplot as plt # data visualization
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv

load_dotenv()  # loads credentials from .env

scope = "user-library-read"

sp = spotipy.Spotify(auth_manager=SpotifyOAuth(scope=scope))

results = sp.current_user_saved_tracks()
for idx, item in enumerate(results['items']):
    track = item['track']
    print(idx, track['artists'][0]['name'], " – ", track['name'])

# def get_top_tracks(limit=50, time_range="medium_term") -> pd.DataFrame:
#     """
#     Fetch user's top tracks and return as a DataFrame.
#     time_range: 'short_term' (4 weeks), 'medium_term' (6 months), 'long_term' (all time)
#     """
#     results = sp.current_user_top_tracks(limit=limit, time_range=time_range)
#     tracks = []
#     for i, item in enumerate(results["items"]):
#         tracks.append({
#             "rank": i + 1,
#             "name": item["name"],
#             "artist": ", ".join(a["name"] for a in item["artists"]),
#             "album": item["album"]["name"],
#             "popularity": item["popularity"],
#             "duration_ms": item["duration_ms"],
#             "explicit": item["explicit"],
#             "id": item["id"],
#         })
#     return pd.DataFrame(tracks)


# def get_recently_played(limit=50) -> pd.DataFrame:
#     """Fetch recently played tracks and return as a DataFrame."""
#     results = sp.current_user_recently_played(limit=limit)
#     tracks = []
#     for item in results["items"]:
#         track = item["track"]
#         tracks.append({
#             "played_at": item["played_at"],
#             "name": track["name"],
#             "artist": ", ".join(a["name"] for a in track["artists"]),
#             "album": track["album"]["name"],
#             "popularity": track["popularity"],
#             "duration_ms": track["duration_ms"],
#             "explicit": track["explicit"],
#             "id": track["id"],
#         })
#     df = pd.DataFrame(tracks)
#     df["played_at"] = pd.to_datetime(df["played_at"])
#     return df


# if __name__ == "__main__":
#     df_top = get_top_tracks()
#     print("Top Tracks:")
#     print(df_top.head())

#     df_recent = get_recently_played()
#     print("\nRecently Played:")
#     print(df_recent.head())
