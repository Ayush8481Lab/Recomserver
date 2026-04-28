import asyncio
import httpx
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from ytmusicapi import YTMusic
import re
import urllib.parse
import html

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
    clean_title = re.sub(r'\(\s*Movie:\s*([^)]+)\)', r'(From "\1")', clean_title, flags=re.IGNORECASE)
    
    # 2. Convert standard quotes to &quot; 
    clean_title = clean_title.replace('"', '&quot;')
    
    # 3. Aggressively remove "Remix" and EVERYTHING after it
    clean_title = re.sub(r'(?i)\bremix\b.*', '', clean_title)
    
    # 4. Remove anything inside square brackets
    clean_title = re.sub(r'\[.*?\]', '', clean_title)
    
    # 5. Remove parentheses EXCEPT if they start with "(From "
    clean_title = re.sub(r'\((?!From\b)[^)]*\)', '', clean_title, flags=re.IGNORECASE)
    
    # 6. Remove common modifier words 
    for kw in FORBIDDEN_KEYWORDS:
        if kw.lower() != 'remix': 
            clean_title = re.sub(rf'\b{kw}\b', '', clean_title, flags=re.IGNORECASE)
    
    # 7. Remove "feat." or "ft."
    clean_title = re.sub(r'\bfeat\.?\b|\bft\.?\b', '', clean_title, flags=re.IGNORECASE)
    
    # 8. Clean up extra spaces and trailing dashes left behind
    clean_title = re.sub(r'\s+', ' ', clean_title)
    clean_title = clean_title.strip('- ') 
    
    return clean_title if clean_title else yt_title

# Async Helper Function: Fetch details from JioSaavn
async def fetch_jiosaavn_data(session: httpx.AsyncClient, yt_title: str, yt_artist: str):
    
    # Step 1: INTELLIGENTLY CLEAN TITLE BEFORE SEARCHING
    original_title = extract_original_title(yt_title)
    
    query_string = f"{original_title} {yt_artist}".strip()
    encoded_query = urllib.parse.quote(query_string, safe="()")
    
    url = f"https://ayushm-psi.vercel.app/api/search/songs?query={encoded_query}&page=1"
    
    try:
        response = await session.get(url, timeout=15.0)
        
        if response.status_code != 200:
            return None
            
        data = response.json()
        
        if data.get("success") and data.get("data", {}).get("results"):
            results = data["data"]["results"]
            
            # --- STEP 3: STRICTLY FILTER AND FIND PERFECT MATCH ---
            
            # 3A. Gather all clean tracks (no forbidden keywords)
            clean_results =[]
            for result in results:
                jio_title_lower = result.get("name", "").lower()
                is_clean = True
                for kw in FORBIDDEN_KEYWORDS:
                    if re.search(rf'\b{kw}\b', jio_title_lower):
                        is_clean = False
                        break
                if is_clean:
                    clean_results.append(result)
            
            best_result = None
            
            if clean_results:
                # Normalize target so &quot; becomes " just for matching comparison
                target_match = html.unescape(original_title).lower().strip()
                
                # 3B. Pass 1: Prioritize an EXACT MATCH 
                for res in clean_results:
                    # Unescape API response too (just in case they return " or &quot;)
                    res_name_norm = html.unescape(res.get("name", "")).lower().strip()
                    
                    if res_name_norm == target_match:
                        best_result = res
                        break
                
                # 3C. Pass 2: If no perfect exact match is found, fallback to top clean result
                if not best_result:
                    best_result = clean_results[0]
            else:
                # Fallback: If absolutely everything had a dirty keyword, grab the very first result
                best_result = results[0]
                
            # --- EXTRACT DATA FROM THE BEST MATCH ---
            jio_title = best_result.get("name", original_title)
            
            primary_artists = best_result.get("artists", {}).get("primary",[])
            artist_names = ", ".join([a["name"] for a in primary_artists])
            
            images = best_result.get("image",[])
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
        print(f"Failed to fetch JioSaavn data for '{query_string}': {e}")
        
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
