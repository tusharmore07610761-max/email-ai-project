"""Convenience entry point: `python main.py` runs the same Flask app as `python app.py`."""

import os

from app import app

if __name__ == '__main__':
    app.run(debug=os.environ.get('FLASK_DEBUG') == '1')