import asyncio
import httpx
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from ytmusicapi import YTMusic
import re

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

# Initialize YTMusic
yt = YTMusic()

# Keywords that usually indicate a modified track
MODIFIER_KEYWORDS =[
    "remix", "slowed", "reverb", "lofi", "instrumental", 
    "karaoke", "mashup", "acoustic", "unplugged", "cover", "8d", "sped up"
]

# Async Helper Function: Fetch details from JioSaavn
async def fetch_jiosaavn_data(session: httpx.AsyncClient, yt_title: str, yt_artist: str):
    query = f"{yt_title} {yt_artist}"
    url = "https://ayushm-psi.vercel.app/api/search/songs"
    
    try:
        response = await session.get(url, params={"query": query}, timeout=15.0)
        
        if response.status_code != 200:
            return None
            
        data = response.json()
        
        if data.get("success") and data.get("data", {}).get("results"):
            results = data["data"]["results"]
            
            # --- SMART MATCHING LOGIC ---
            yt_title_lower = yt_title.lower()
            
            # Find out if the original YouTube song genuinely IS a remix/slowed version
            allowed_modifiers = [kw for kw in MODIFIER_KEYWORDS if kw in yt_title_lower]
            
            best_result = None
            
            # Iterate through results to find a clean match
            for result in results:
                jio_title_lower = result.get("name", "").lower()
                
                is_clean = True
                for kw in MODIFIER_KEYWORDS:
                    # If the JioSaavn title has a modifier (like "reverb") 
                    # but the original YT title DID NOT have it, reject this result.
                    if kw in jio_title_lower and kw not in allowed_modifiers:
                        is_clean = False
                        break
                
                if is_clean:
                    best_result = result
                    break # Found the perfect clean original track!
            
            # Fallback: If all results are somehow modified, just grab the first one
            if not best_result:
                best_result = results[0]
                
            # --- EXTRACT DATA FROM THE BEST MATCH ---
            jio_title = best_result.get("name", yt_title)
            
            primary_artists = best_result.get("artists", {}).get("primary",[])
            artist_names = ", ".join([a["name"] for a in primary_artists])
            
            images = best_result.get("image", [])
            banner_url = images[-1]["url"] if images else ""
            
            downloads = best_result.get("downloadUrl",[])
            stream_url = ""
            for d in downloads:
                if d.get("quality") == "320kbps":
                    stream_url = d["url"]
                    break
            
            if not stream_url and downloads:
                stream_url = downloads[-1]["url"]
                
            perma_url = best_result.get("url", "")
            
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
        
        # 3. Iterate over related sections safely
        for section in related_data:
            for track in section.get('contents',[]):
                # Make sure it's a valid track
                if 'videoId' not in track:
                    continue
                
                # Skip the currently playing song
                if track.get('videoId') == vid:
                    continue
                
                title = track.get('title', "")
                artist_name = ", ".join([a['name'] for a in track.get('artists',[]) if 'name' in a])
                
                yt_search_queries.append((title, artist_name))
        
        # 4. Remove any duplicate tracks
        unique_queries = list(dict.fromkeys(yt_search_queries))
        
        # 5. Process all JioSaavn requests SIMULTANEOUSLY
        async with httpx.AsyncClient() as session:
            tasks =[fetch_jiosaavn_data(session, title, artist) for title, artist in unique_queries]
            jiosaavn_results = await asyncio.gather(*tasks)
        
        # 6. Filter out any None values 
        final_recommendations =[res for res in jiosaavn_results if res is not None]
        
        return {"recommendations": final_recommendations}

    except Exception as e:
        return {"error": "Failed to fetch recommendations", "details": str(e)}

# For local testing
if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=8000)
