from flask import Flask, jsonify, send_from_directory

app = Flask(__name__, static_folder=".", static_url_path="")

@app.route("/")
def home():
    return send_from_directory(".", "index.html")

@app.route("/styles.css")
def styles():
    return send_from_directory(".", "styles.css")

@app.route("/overlay.js")
def script():
    return send_from_directory(".", "overlay.js")

@app.route("/api/current")
def current():
    data = {
        "meeting": "Bahrain Grand Prix",
        "session": "Qualifying",
        "rows": [
            { "name": "Charles Leclerc", "team": "Ferrari", "color": "#dc0000", "time": 91.234, "gap": 0.000 },
            { "name": "Lando Norris", "team": "McLaren", "color": "#ff8700", "time": 91.450, "gap": 0.216 },
            { "name": "Max Verstappen", "team": "Red Bull Racing", "color": "#3671c6", "time": 91.600, "gap": 0.366 }
        ]
    }
    return jsonify(data)

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
