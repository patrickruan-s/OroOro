OroOro Spotify App

1. Clone and enter the repo

  git clone https://github.com/patrickruan-s/OroOro.git
  cd spotify_analysis

  2. Create a virtual environment

  python3 -m venv .venv
  source .venv/bin/activate  # Windows:
  .venv\Scripts\activate

  3. Install dependencies

  pip install -r requirements.txt

  4. Create a Spotify app

  1. Go to developer.spotify.com/dashboard and
  create an app
  2. Under Edit Settings → Redirect URIs, add:
  http://127.0.0.1:8888/callback

  5. Configure credentials

  Create a .env file in the project root:
  SPOTIPY_CLIENT_ID=your_client_id
  SPOTIPY_CLIENT_SECRET=your_client_secret
  SPOTIPY_REDIRECT_URI=http://127.0.0.1:8888/callba
  ck

  6. Run

  python3 main.py

  A browser window will open asking you to
  authorize the app. After approving, the token is
  cached in .cache for future runs.
