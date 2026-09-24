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

    cp .env.example .env
# .env anpassen (Passwörter, ggf. BASE_URL/Basic-Auth)
docker compose up -d --build

Danach unbedingt Browser-Cache leeren (Strg+Shift+R), da app.js sonst gecacht bleibt. Falls die Historie fehlt: curl -X POST http://<host>:8080/api/rescan erzwingt einen kompletten Neu-Scan aller Jahre/Ordner.