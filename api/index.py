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

yt = YTMusic()

# Fake Browser Headers to bypass Vercel Bot blocks
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Connection": "keep-alive"
}

# --- IMPORTANT: QUEUE LIMITER ---
# This prevents your custom Spotify API from crashing by only allowing 5 requests at a time.
spotify_semaphore = asyncio.Semaphore(5)

# ==========================================
# EXACT & ROBUST SPOTIFY MATCHING LOGIC 
# ==========================================
def clean_string(s: str) -> str:
    if not s:
        return ""
    s = str(s).lower()
    s = re.sub(r'[^\w\s]|_', '', s)
    s = re.sub(r'\s+', ' ', s)
    return s.strip()

def perform_matching(api_data, target_track, target_artist):
    if not api_data:
        return None
        
    tracks =[]
    
    # Extract tracks array no matter what JSON structure your API returns
    if isinstance(api_data, list):
        tracks = api_data
    elif isinstance(api_data, dict):
        if 'data' in api_data and isinstance(api_data['data'], list):
            tracks = api_data['data']
        elif 'results' in api_data and isinstance(api_data['results'], list):
            tracks = api_data['results']
        else:
            tracks_obj = api_data.get('tracks',[])
            if isinstance(tracks_obj, dict) and 'items' in tracks_obj:
                tracks = tracks_obj['items']
            elif isinstance(tracks_obj, list):
                tracks = tracks_obj
                
    if not tracks or not isinstance(tracks, list):
        return None
        
    t_title = clean_string(target_track)
    t_artist = clean_string(target_artist)
    
    best_match = None
    highest_score = 0
    
    for item in tracks:
        track = item.get('data') if isinstance(item, dict) and 'data' in item else item
        if not track or not isinstance(track, dict):
            continue
            
        r_title = clean_string(track.get('name', track.get('title', '')))
        
        r_artists =[]
        artists_data = track.get('artists', track.get('artist',[]))
        
        if isinstance(artists_data, str):
            r_artists = [clean_string(a.strip()) for a in artists_data.split(',')]
        elif isinstance(artists_data, dict) and 'items' in artists_data:
            for a in artists_data['items']:
                if 'profile' in a and 'name' in a['profile']:
                    r_artists.append(clean_string(a['profile']['name']))
                elif 'name' in a:
                    r_artists.append(clean_string(a['name']))
        elif isinstance(artists_data, list):
            for a in artists_data:
                if isinstance(a, str):
                    r_artists.append(clean_string(a))
                elif isinstance(a, dict) and 'name' in a:
                    r_artists.append(clean_string(a['name']))
                    
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
        # TIMEOUT INCREASED TO 45 SECONDS to allow your API to finish processing
        response = await session.get(url, params={"query": query}, timeout=45.0)
        
        if response.status_code == 200:
            # 1. Fallback: If your API returns raw text instead of JSON
            if "open.spotify.com" in response.text and not response.text.strip().startswith("{") and not response.text.strip().startswith("["):
                return response.text.strip()
                
            api_data = response.json()
            
            # 2. Fallback: If your API returns a single dictionary with the URL already parsed
            if isinstance(api_data, dict):
                for key in ['spotify_url', 'url', 'link', 'spotify', 'external_url']:
                    if key in api_data and isinstance(api_data[key], str) and 'spotify.com' in api_data[key]:
                        return api_data[key]
                if 'id' in api_data and 'name' in api_data and isinstance(api_data['id'], str):
                    return f"https://open.spotify.com/track/{api_data['id']}"

            # 3. Primary: Perform the exact RapidAPI Array Matching Logic
            match = perform_matching(api_data, title, artist)
            
            if match:
                if 'id' in match:
                    return f"https://open.spotify.com/track/{match['id']}"
                elif 'url' in match and 'spotify.com' in str(match['url']):
                    return match['url']
                elif 'external_urls' in match and 'spotify' in match['external_urls']:
                    return match['external_urls']['spotify']
                    
    except httpx.TimeoutException:
        print(f"Spotify Timeout for {query} (Took longer than 45s)")
    except Exception as e:
        print(f"Failed to fetch Spotify data for {query}: {e}")
        
    return ""

async def fetch_jiosaavn_data(session: httpx.AsyncClient, title: str, artist: str):
    query = f"{title} {artist}"
    url = "https://ayushm-psi.vercel.app/api/search/songs"
    
    try:
        # JioSaavn is fast, so we keep this timeout low
        response = await session.get(url, params={"query": query}, timeout=10.0)
        
        if response.status_code == 200:
            data = response.json()
            if data.get("success") and data.get("data", {}).get("results"):
                top_result = data["data"]["results"][0]
                
                jio_title = top_result.get("name", title)
                primary_artists = top_result.get("artists", {}).get("primary",[])
                artist_names = ", ".join([a["name"] for a in primary_artists])
                if not artist_names:
                    artist_names = artist
                
                images = top_result.get("image", [])
                banner_url = images[-1]["url"] if images else ""
                
                downloads = top_result.get("downloadUrl",[])
                stream_url = ""
                for d in downloads:
                    if d.get("quality") == "320kbps":
                        stream_url = d["url"]
                        break
                if not stream_url and downloads:
                    stream_url = downloads[-1]["url"]
                    
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

async def process_song(session: httpx.AsyncClient, yt_title: str, yt_artist: str):
    # 1. Fetch JioSaavn (Fast - runs instantly)
    jio_data = await fetch_jiosaavn_data(session, yt_title, yt_artist)
    if not jio_data:
        return None
        
    # 2. Fetch Spotify (Slow - Uses Semaphore to Queue up safely)
    async with spotify_semaphore:
        spotify_link = await fetch_spotify_link(session, jio_data["Title"], jio_data["Artists"])
    
    jio_data["Spotify Link"] = spotify_link
    return jio_data


# ==========================================
# MAIN API ENDPOINT
# ==========================================
@app.get("/app/api")
async def get_recommendations(vid: str = Query(..., description="The Video ID of the song")):
    try:
        watch_playlist = yt.get_watch_playlist(videoId=vid, limit=15)
        
        yt_search_queries = []
        for track in watch_playlist.get('tracks',[]):
            if track.get('videoId') == vid:
                continue
            
            artist_name = ", ".join([a['name'] for a in track.get('artists',[]) if 'name' in a])
            yt_search_queries.append((track.get('title'), artist_name))
        
        # Inject Browser Headers & Follow redirects to bypass Vercel Blocks
        async with httpx.AsyncClient(headers=BROWSER_HEADERS, follow_redirects=True) as session:
            tasks =[process_song(session, title, artist) for title, artist in yt_search_queries]
            results = await asyncio.gather(*tasks)
        
        final_recommendations =[res for res in results if res is not None]
        
        return {"recommendations": final_recommendations}

    except Exception as e:
        return {"error": "Failed to fetch recommendations", "details": str(e)}
