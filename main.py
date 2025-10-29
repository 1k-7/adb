from flask import Flask
import os

app = Flask(__name__)

@app.route('/')
def health_check():
    """
    This endpoint is checked by Render/Koyeb to ensure
    the service is "healthy" and running.
    """
    return "Bot manager is alive.", 200

if __name__ == '__main__':
    # Get port from environment variables (e.g., set by Render)
    port = int(os.environ.get('PORT', 8080))
    # Run the app
    app.run(host='0.0.0.0', port=port)
