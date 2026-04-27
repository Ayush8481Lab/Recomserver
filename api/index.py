import asyncio
import httpx
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from ytmusicapi import YTMusic
import re

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

yt = YTMusic()

# --- INTELLIGENCE DATA ---
FORBIDDEN_KEYWORDS =[
    "remix", "slowed", "reverb", "lofi", "instrumental", "karaoke", 
    "mashup", "acoustic", "unplugged", "cover", "8d", "sped up", 
    "teaser", "trailer", "promo", "dialogue", "reprise", "dj", 
    "bass boosted", "mix", "edit", "tiktok", "viral"
]

def extract_original_title(yt_title: str) -> str:
    """
    Highly intelligent cleaner for YouTube titles.
    """
    clean_title = yt_title
    
    # 1. Transform (Movie: Name) -> (From "Name")
    # Captures the name inside and reformats it.
    clean_title = re.sub(r'\(\s*Movie:\s*([^)]+)\)', r'(From "\1")', clean_title, flags=re.IGNORECASE)
    
    # 2. Convert standard quotes to &quot; because the search API strictly expects this
    clean_title = clean_title.replace('"', '&quot;')
    
    # 3. Aggressively remove "Remix" and EVERYTHING after it
    # Matches "remix" and deletes the rest of the string (e.g. "Taras Remix by DJ Notorious" -> "Taras ")
    clean_title = re.sub(r'(?i)\bremix\b.*', '', clean_title)
    
    # 4. Remove anything inside square brackets:[Official Video], [Slowed], etc.
    clean_title = re.sub(r'\[.*?\]', '', clean_title)
    
    # 5. Remove parentheses EXCEPT if they start with "(From "
    # This protects "(From &quot;Gadar 2&quot;)" but correctly deletes "(Official Video)"
    clean_title = re.sub(r'\((?!From\b)[^)]*\)', '', clean_title, flags=re.IGNORECASE)
    
    # 6. Remove common modifier words from the remaining text
    for kw in FORBIDDEN_KEYWORDS:
        if kw.lower() != 'remix': # Remix is already handled aggressively above
            clean_title = re.sub(rf'\b{kw}\b', '', clean_title, flags=re.IGNORECASE)
    
    # 7. Remove "feat." or "ft."
    clean_title = re.sub(r'\bfeat\.?\b|\bft\.?\b', '', clean_title, flags=re.IGNORECASE)
    
    # 8. Clean up extra spaces and trailing dashes left behind
    clean_title = re.sub(r'\s+', ' ', clean_title)
    clean_title = clean_title.strip('- ') # Removes any hanging hyphens
    
    # Fallback to original if our cleaning accidentally removed the whole title
    return clean_title if clean_title else yt_title

# Async Helper Function: Fetch details from JioSaavn
async def fetch_jiosaavn_data(session: httpx.AsyncClient, yt_title: str, yt_artist: str):
    
    # Step 1: INTELLIGENTLY CLEAN TITLE BEFORE SEARCHING
    original_title = extract_original_title(yt_title)
    
    # Query JioSaavn with the perfectly cleaned title
    query = f"{original_title} {yt_artist}"
    url = "https://ayushm-psi.vercel.app/api/search/songs"
    
    try:
        response = await session.get(url, params={"query": query}, timeout=15.0)
        
        if response.status_code != 200:
            return None
            
        data = response.json()
        
        if data.get("success") and data.get("data", {}).get("results"):
            results = data["data"]["results"]
            
            best_result = None
            
            # Step 2: STRICTLY FILTER JIOSAAVN RESULTS
            for result in results:
                jio_title_lower = result.get("name", "").lower()
                
                is_clean = True
                for kw in FORBIDDEN_KEYWORDS:
                    # If the JioSaavn title contains "teaser", "remix", "lofi" etc., REJECT IT!
                    if re.search(rf'\b{kw}\b', jio_title_lower):
                        is_clean = False
                        break
                
                if is_clean:
                    # We found the perfect, unmodified original song!
                    best_result = result
                    break
            
            # Fallback: If absolutely ALL results have a forbidden word, grab the first one
            if not best_result:
                best_result = results[0]
                
            # --- EXTRACT DATA FROM THE BEST MATCH ---
            jio_title = best_result.get("name", original_title)
            
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
                "Original YT Search": yt_title,
                "Cleaned Title Searched": original_title,
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
        watch_playlist = yt.get_watch_playlist(videoId=vid)
        related_browse_id = watch_playlist.get('related')
        
        if not related_browse_id:
            return {"error": "No related songs found for this video.", "recommendations":[]}
            
        related_data = yt.get_song_related(related_browse_id)
        
        yt_search_queries =[]
        
        for section in related_data:
            for track in section.get('contents',[]):
                if 'videoId' not in track:
                    continue
                
                if track.get('videoId') == vid:
                    continue
                
                title = track.get('title', "")
                artist_name = ", ".join([a['name'] for a in track.get('artists',[]) if 'name' in a])
                
                yt_search_queries.append((title, artist_name))
        
        unique_queries = list(dict.fromkeys(yt_search_queries))
        
        async with httpx.AsyncClient() as session:
            tasks =[fetch_jiosaavn_data(session, title, artist) for title, artist in unique_queries]
            jiosaavn_results = await asyncio.gather(*tasks)
        
        final_recommendations =[res for res in jiosaavn_results if res is not None]
        
        return {"recommendations": final_recommendations}

    except Exception as e:
        return {"error": "Failed to fetch recommendations", "details": str(e)}

# For local testing
if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=8000)
