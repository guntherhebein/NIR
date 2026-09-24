"# NIR" 
Dieses toll liest den Apotec NIR aus, prüft auf neue PDFs mit Messdaten und kopiert diese in eine Mongo DB.
Die IP ist derzeit "10.139.13.121"

NIR/
├── docker-compose.yml
├── .env.example
├── watcher/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── watcher.py
└── webapp/
    ├── Dockerfile
    ├── requirements.txt
    ├── app.py
    ├── templates/index.html
    └── static/{style.css, app.js}