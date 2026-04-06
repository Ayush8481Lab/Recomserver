import asyncio
import httpx
import re
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from ytmusicapi import YTMusic

# Initialize FastAPI
app = FastAPI()

# --- ENABLE CORS FOR YOUR APP ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],     
    allow_credentials=False, 
    allow_methods=["*"],     
    allow_headers=["*"],     
)

# Initialize YouTube Music API
yt = YTMusic()

# ==========================================
# EXACT SPOTIFY MATCHING LOGIC (Translated from JS)
# ==========================================
def clean_string(s: str) -> str:
    if not s:
        return ""
    s = str(s).lower()
    # Remove non-alphanumeric characters except spaces
    s = re.sub(r'[^\w\s]|_', '', s)
    # Remove extra spaces
    s = re.sub(r'\s+', ' ', s)
    return s.strip()

def perform_matching(api_data, target_track, target_artist):
    if not api_data or not isinstance(api_data, dict):
        return None
        
    # Handle both Custom/Standard Spotify API and RapidAPI formats seamlessly
    tracks = api_data.get('tracks',[])
    if isinstance(tracks, dict) and 'items' in tracks:
        tracks = tracks['items']
        
    if not tracks or not isinstance(tracks, list):
        return None
        
    t_title = clean_string(target_track)
    t_artist = clean_string(target_artist)
    
    best_match = None
    highest_score = 0
    
    for item in tracks:
        # Check if wrapped in "data" (RapidAPI) or direct (Standard Spotify)
        track = item.get('data') if 'data' in item else item
        if not track:
            continue
            
        r_title = clean_string(track.get('name', ''))
        
        # Extract artists robustly
        r_artists =[]
        artists_data = track.get('artists', {})
        if isinstance(artists_data, dict) and 'items' in artists_data:
            for a in artists_data['items']:
                if 'profile' in a and 'name' in a['profile']:
                    r_artists.append(clean_string(a['profile']['name']))
                elif 'name' in a:
                    r_artists.append(clean_string(a['name']))
        elif isinstance(artists_data, list):
            for a in artists_data:
                if 'name' in a:
                    r_artists.append(clean_string(a['name']))
                    
        # Score Calculation exactly like your HTML logic
        score = 0
        artist_matched = False
        
        if len(t_artist) > 0:
            for ra in r_artists:
                if ra == t_artist:
                    score += 100
                    artist_matched = True
                    break
                elif ra in t_artist or t_artist in ra:
                    score += 80
                    artist_matched = True
                    break
            if not artist_matched:
                score = 0
        else:
            score += 50
            
        if score > 0:
            if r_title == t_title:
                score += 100
            elif r_title.startswith(t_title) or t_title.startswith(r_title):
                score += 80
            elif t_title in r_title or r_title in t_title:
                score += 50
                
        if score > highest_score:
            highest_score = score
            best_match = track
            
    return best_match

# ==========================================
# ASYNC FETCHERS
# ==========================================
async def fetch_spotify_link(session: httpx.AsyncClient, title: str, artist: str):
    search_artist = " ".join(artist.split(",")[:2]).strip() if artist else ""
    query = f"{title} {search_artist}".strip()
    url = "https://ayushspot.vercel.app/api"
    
    try:
        response = await session.get(url, params={"query": query}, timeout=10.0)
        if response.status_code == 200:
            api_data = response.json()
            match = perform_matching(api_data, title, artist)
            if match and 'id' in match:
                return f"https://open.spotify.com/track/{match['id']}"
    except Exception as e:
        print(f"Failed to fetch Spotify data for {query}: {e}")
        
    return ""

async def fetch_jiosaavn_data(session: httpx.AsyncClient, title: str, artist: str):
    query = f"{title} {artist}"
    url = "https://ayushm-psi.vercel.app/api/search/songs"
    
    try:
        response = await session.get(url, params={"query": query}, timeout=10.0)
        
        if response.status_code == 200:
            data = response.json()
            if data.get("success") and data.get("data", {}).get("results"):
                top_result = data["data"]["results"][0]
                
                # 1. Title
                jio_title = top_result.get("name", title)
                
                # 2. Artists
                primary_artists = top_result.get("artists", {}).get("primary",[])
                artist_names = ", ".join([a["name"] for a in primary_artists])
                if not artist_names:
                    artist_names = artist
                
                # 3. Banner
                images = top_result.get("image", [])
                banner_url = images[-1]["url"] if images else ""
                
                # 4. Stream Link
                downloads = top_result.get("downloadUrl",[])
                stream_url = ""
                for d in downloads:
                    if d.get("quality") == "320kbps":
                        stream_url = d["url"]
                        break
                if not stream_url and downloads:
                    stream_url = downloads[-1]["url"]
                    
                # 5. JioSaavn Perma URL
                perma_url = top_result.get("url", "")
                
                return {
                    "Title": jio_title,
                    "Artists": artist_names,
                    "Banner": banner_url,
                    "Stream": stream_url,
                    "Perma URL": perma_url
                }
    except Exception as e:
        print(f"Failed to fetch JioSaavn data for {query}: {e}")
        
    return None

# Combined Task Handler for Speed
async def process_song(session: httpx.AsyncClient, yt_title: str, yt_artist: str):
    # Step 1: Get JioSaavn Data
    jio_data = await fetch_jiosaavn_data(session, yt_title, yt_artist)
    if not jio_data:
        return None
        
    # Step 2: Use exact JioSaavn Title/Artist to search Spotify
    spotify_link = await fetch_spotify_link(session, jio_data["Title"], jio_data["Artists"])
    
    # Step 3: Combine Results
    jio_data["Spotify Link"] = spotify_link
    return jio_data


# ==========================================
# MAIN API ENDPOINT
# ==========================================
@app.get("/app/api")
async def get_recommendations(vid: str = Query(..., description="The Video ID of the song")):
    try:
        watch_playlist = yt.get_watch_playlist(videoId=vid, limit=15)
        
        yt_search_queries =[]
        for track in watch_playlist.get('tracks',[]):
            if track.get('videoId') == vid:
                continue
            
            artist_name = ", ".join([a['name'] for a in track.get('artists',[]) if 'name' in a])
            yt_search_queries.append((track.get('title'), artist_name))
        
        # Process all songs CONCURRENTLY
        async with httpx.AsyncClient() as session:
            tasks = [process_song(session, title, artist) for title, artist in yt_search_queries]
            
            # This executes JioSaavn -> Spotify for all 15 songs simultaneously
            results = await asyncio.gather(*tasks)
        
        final_recommendations = [res for res in results if res is not None]
        
        return {"recommendations": final_recommendations}

    except Exception as e:
        return {"error": "Failed to fetch recommendations", "details": str(e)}
