import asyncio
import httpx
from typing import List
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from ytmusicapi import YTMusic

# Initialize FastAPI
app = FastAPI()

# --- ENABLE CORS ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],     
    allow_credentials=False, 
    allow_methods=["*"],     
    allow_headers=["*"],     
)

# Initialize YTMusic Anonymously
yt = YTMusic()

# Async Helper Function: Fetch details from JioSaavn
async def fetch_jiosaavn_data(session: httpx.AsyncClient, title: str, artist: str):
    query = f"{title} {artist}"
    url = "https://ayushm-psi.vercel.app/api/search/songs"
    
    try:
        response = await session.get(url, params={"query": query}, timeout=15.0)
        
        if response.status_code != 200:
            return None
            
        data = response.json()
        
        if data.get("success") and data.get("data", {}).get("results"):
            top_result = data["data"]["results"][0]
            
            jio_title = top_result.get("name", title)
            
            primary_artists = top_result.get("artists", {}).get("primary", [])
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


@app.get("/api/personalized")
async def get_history_based_recommendations(
    # Accepts multiple vids from the URL string
    vids: List[str] = Query(..., description="List of recently played Video IDs to simulate history")
):
    try:
        # SAFETY NET: Limit to the last 20 songs to prevent YouTube payload errors 
        # and to keep the algorithm's mood detection highly accurate.
        recent_history_vids = vids[-20:] 
        
        # Pass the array to YouTube. It treats this as the current session queue
        watch_playlist = yt.get_watch_playlist(videoIds=recent_history_vids)
        
        yt_search_queries =[]
        for track in watch_playlist.get('tracks',[]):
            track_vid = track.get('videoId')
            
            # Skip if the user has already listened to this song in their history
            if track_vid in recent_history_vids:
                continue
                
            # Failsafe: Ensure track has a title
            if not track.get('title'):
                continue
            
            artist_name = ", ".join([a['name'] for a in track.get('artists',[]) if 'name' in a])
            yt_search_queries.append((track.get('title'), artist_name))
        
        # Limit to the top 15 recommendations so JioSaavn fetch is super fast
        yt_search_queries = yt_search_queries[:15]
        
        # Process JioSaavn requests SIMULTANEOUSLY
        async with httpx.AsyncClient() as session:
            tasks =[fetch_jiosaavn_data(session, title, artist) for title, artist in yt_search_queries]
            jiosaavn_results = await asyncio.gather(*tasks)
        
        # Filter out any songs JioSaavn couldn't find
        final_recommendations = [res for res in jiosaavn_results if res is not None]
        
        return {"recommendations": final_recommendations}

    except Exception as e:
        return {"error": "Failed to fetch recommendations", "details": str(e)}

# For local testing
if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=8000)
