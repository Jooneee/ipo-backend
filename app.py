from flask import Flask, jsonify
from flask_cors import CORS
from scraper import get_ipo_list, get_ipo_detail
import os

app = Flask(__name__)
CORS(app)


@app.route('/api/ipo/list')
def ipo_list():
    try:
        data = get_ipo_list()
        return jsonify({'success': True, 'data': data})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/ipo/<code>')
def ipo_detail(code):
    try:
        data = get_ipo_detail(code)
        return jsonify({'success': True, 'data': data})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/health')
def health():
    return jsonify({'status': 'ok'})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
