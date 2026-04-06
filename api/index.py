import asyncio
import httpx
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from ytmusicapi import YTMusic

# Initialize FastAPI
app = FastAPI()

# --- ENABLE CORS FOR YOUR APP ---
# This allows your frontend (web/mobile app) to make requests to this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins. You can change "*" to your app's domain later for security.
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods (GET, POST, etc.)
    allow_headers=["*"],  # Allows all headers
)

yt = YTMusic()

# Async Helper Function: Fetch details from JioSaavn
async def fetch_jiosaavn_data(session: httpx.AsyncClient, title: str, artist: str):
    # Create the search query combining title and artist for best accuracy
    query = f"{title} {artist}"
    url = f"https://ayushm-psi.vercel.app/api/search/songs?query={query}"
    
    try:
        # Fetch the data from the JioSaavn API
        response = await session.get(url, timeout=10.0)
        data = response.json()
        
        # Check if we got successful results
        if data.get("success") and data.get("data", {}).get("results"):
            # Get the top (first) result
            top_result = data["data"]["results"][0]
            
            # 1. Title
            jio_title = top_result.get("name", title)
            
            # 2. Artists (Comma-separated from primary_artists)
            primary_artists = top_result.get("artists", {}).get("primary", [])
            artist_names = ", ".join([a["name"] for a in primary_artists])
            
            # 3. Banner (Highest quality - usually the last item in the list)
            images = top_result.get("image",[])
            banner_url = images[-1]["url"] if images else ""
            
            # 4. Stream Link (Only 320kbps)
            downloads = top_result.get("downloadUrl",[])
            stream_url = ""
            for d in downloads:
                if d.get("quality") == "320kbps":
                    stream_url = d["url"]
                    break
            
            # If 320kbps somehow doesn't exist, fallback to the highest available quality
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
        
    return None # Return None if search fails

@app.get("/app/api")
async def get_recommendations(vid: str = Query(..., description="The Video ID of the song")):
    try:
        # 1. Get YouTube Music Recommendations
        # Limiting to 15 to ensure we don't hit Vercel's 10-second timeout limit while scraping JioSaavn
        watch_playlist = yt.get_watch_playlist(videoId=vid, limit=15)
        
        yt_search_queries = []
        for track in watch_playlist.get('tracks',[]):
            if track.get('videoId') == vid:
                continue
            
            artist_name = ", ".join([a['name'] for a in track.get('artists',[]) if 'name' in a])
            yt_search_queries.append((track.get('title'), artist_name))
        
        # 2. Process all JioSaavn requests SIMULTANEOUSLY for extreme speed
        async with httpx.AsyncClient() as session:
            # Create a list of tasks
            tasks =[fetch_jiosaavn_data(session, title, artist) for title, artist in yt_search_queries]
            
            # Execute all tasks at the exact same time
            jiosaavn_results = await asyncio.gather(*tasks)
        
        # 3. Filter out any None values (in case a song wasn't found on JioSaavn)
        final_recommendations =[res for res in jiosaavn_results if res is not None]
        
        return {"recommendations": final_recommendations}

    except Exception as e:
        return {"error": "Failed to fetch recommendations", "details": str(e)}
