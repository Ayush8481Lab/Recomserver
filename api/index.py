from fastapi import FastAPI, Query
from ytmusicapi import YTMusic

# Initialize FastAPI and YTMusic
app = FastAPI()
yt = YTMusic()

# Your requested endpoint: /app/api?vid=YOUR_VIDEO_ID
@app.get("/app/api")
def get_recommendations(vid: str = Query(..., description="The Video ID of the song")):
    try:
        # Fetch the auto-generated radio queue for this video ID
        watch_playlist = yt.get_watch_playlist(videoId=vid, limit=20)
        
        recommended_songs =[]
        
        # Loop through the tracks returned by YouTube Music
        for track in watch_playlist.get('tracks',[]):
            
            # The first track is the song you searched for. We skip it.
            if track.get('videoId') == vid:
                continue
                
            # Combine multiple artists into a single string if there are multiple
            artist_name = ", ".join([artist['name'] for artist in track.get('artists',[]) if 'name' in artist])
            
            # FORMATTED OUTPUT: Only Title and Artist Name!
            song_data = {
                "Title": track.get('title'),
                "Artist Name": artist_name
            }
            
            recommended_songs.append(song_data)
            
        return {"recommendations": recommended_songs}

    except Exception as e:
        return {"error": "Failed to fetch recommendations", "details": str(e)}
