import asyncio
import httpx
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

# Initialize YTMusic (Location will auto-detect from your server IP)
yt = YTMusic()

# Async Helper Function: Fetch details from JioSaavn
async def fetch_jiosaavn_data(session: httpx.AsyncClient, title: str, artist: str):
    query = f"{title} {artist}"
    url = "https://ayushm-psi.vercel.app/api/search/songs"
    
    try:
        # Using 'params' securely encodes spaces and special characters
        response = await session.get(url, params={"query": query}, timeout=15.0)
        
        if response.status_code != 200:
            return None
            
        data = response.json()
        
        if data.get("success") and data.get("data", {}).get("results"):
            top_result = data["data"]["results"][0]
            
            jio_title = top_result.get("name", title)
            
            primary_artists = top_result.get("artists", {}).get("primary",[])
            artist_names = ", ".join([a["name"] for a in primary_artists])
            
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
        print(f"Failed to fetch JioSaavn data for '{query}': {e}")
        
    return None 


@app.get("/api")
async def get_recommendations(vid: str = Query(..., description="The Video ID of the song")):
    try:
        # 1. Get original YouTube Music watch playlist to extract the 'Related' tab browseId
        watch_playlist = yt.get_watch_playlist(videoId=vid)
        related_browse_id = watch_playlist.get('related')
        
        # If there is no related tab available for this song, return early
        if not related_browse_id:
            return {"error": "No related songs found for this video.", "recommendations":[]}
            
        # 2. Fetch the actual contents of the "Related" tab
        related_data = yt.get_song_related(related_browse_id)
        
        yt_search_queries =[]
        
        # 3. 'related_data' returns a list of sections (e.g., "You might also like", "Recommended")
        for section in related_data:
            for track in section.get('contents',[]):
                # Make sure it's a valid track/video
                if 'videoId' not in track:
                    continue
                
                # Skip the currently playing song just in case it appears in the related section
                if track.get('videoId') == vid:
                    continue
                
                title = track.get('title', "")
                artist_name = ", ".join([a['name'] for a in track.get('artists',[]) if 'name' in a])
                
                yt_search_queries.append((title, artist_name))
        
        # 4. Remove any duplicate tracks across different sections while preserving the order
        unique_queries = list(dict.fromkeys(yt_search_queries))
        
        # 5. Process all JioSaavn requests SIMULTANEOUSLY
        async with httpx.AsyncClient() as session:
            tasks =[fetch_jiosaavn_data(session, title, artist) for title, artist in unique_queries]
            jiosaavn_results = await asyncio.gather(*tasks)
        
        # 6. Filter out any None values (in case a song wasn't found)
        final_recommendations =[res for res in jiosaavn_results if res is not None]
        
        return {"recommendations": final_recommendations}

    except Exception as e:
        return {"error": "Failed to fetch recommendations", "details": str(e)}

# For local testing
if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=8000)
