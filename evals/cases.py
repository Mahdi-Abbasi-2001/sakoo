"""Evaluation cases: realistic miniature projects with the answer we expect. Used by tests (deterministic layer, no network)
and by scripts/run_evals.py (the LLM agent, needs LLM_API_KEY). Each case: files, and what a correct plan must contain."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Case:
    name: str
    files: dict[str, str]
    platforms: set[str]  # Liara platforms a correct plan uses (any app)
    dbs: set[str] = field(default_factory=set)  # Liara database types a correct plan creates
    env: set[str] = field(default_factory=set)  # variables the code reads that the plan must list
    readiness: set[str] = field(default_factory=set)  # readiness finding codes that must be reported
    apps: int = 1
    note: str = ""


PKG = lambda deps, extra="": '{"name":"app","scripts":{"start":"node server.js"%s},"dependencies":{%s}}' % (extra, deps)  # noqa: E731

CASES = [
    Case("express-postgres",
         {"package.json": PKG('"express":"^4","pg":"^8","dotenv":"^16"'),
          "server.js": "const express=require('express');const {Pool}=require('pg');\nconst pool=new Pool({connectionString:process.env.DATABASE_URL});\nconst app=express();\napp.listen(process.env.PORT||3000,'0.0.0.0');\nconst secret=process.env.JWT_SECRET;\n"},
         {"node"}, {"postgres"}, {"DATABASE_URL", "JWT_SECRET", "PORT"}),
    Case("nextjs-prisma-postgres",
         {"package.json": '{"name":"web","scripts":{"build":"next build","start":"next start"},"dependencies":{"next":"14","react":"18","@prisma/client":"5"}}',
          "prisma/schema.prisma": 'datasource db { provider = "postgresql"\n url = env("DATABASE_URL") }\n',
          "src/app/page.tsx": "export default function P(){return <div>hi {process.env.NEXT_PUBLIC_SITE}</div>}\n"},
         {"next"}, {"postgres"}, {"DATABASE_URL"}),
    Case("django-postgres",
         {"requirements.txt": "Django>=5\npsycopg2-binary\ngunicorn\n", "manage.py": "#!/usr/bin/env python\n",
          "mysite/settings.py": "import os\nSECRET_KEY=os.environ['SECRET_KEY']\nDEBUG=True\nALLOWED_HOSTS=['*']\nDATABASES={'default':{'ENGINE':'django.db.backends.postgresql','HOST':os.environ['DB_HOST']}}\n"},
         {"django"}, {"postgres"}, {"SECRET_KEY", "DB_HOST"}, {"debug_enabled", "allowed_hosts_wildcard"}),
    Case("flask-sqlite",
         {"requirements.txt": "flask\ngunicorn\n", "app.py": "import sqlite3\nfrom flask import Flask\napp=Flask(__name__)\nif __name__=='__main__':\n    app.run(host='127.0.0.1',port=5000,debug=True)\n"},
         {"flask"}, set(), set(), {"sqlite_data_loss", "binds_localhost", "debug_enabled"}),
    Case("laravel-mysql",
         {"composer.json": '{"require":{"laravel/framework":"^11"}}', "artisan": "#!/usr/bin/env php\n",
          ".env.example": "APP_KEY=\nDB_CONNECTION=mysql\nDB_HOST=127.0.0.1\n"},
         {"laravel"}, {"mysql"}, {"APP_KEY", "DB_HOST"}),
    Case("react-vite-static",
         {"package.json": '{"name":"ui","scripts":{"build":"vite build"},"dependencies":{"react":"18","react-dom":"18"},"devDependencies":{"vite":"5"}}',
          "index.html": "<div id=root></div>", "src/main.jsx": "console.log(import.meta.env.VITE_API_URL)\n"},
         {"react"}, set(), set()),
    Case("go-http",
         {"go.mod": "module example.com/api\n\ngo 1.22\n", "main.go": 'package main\nimport ("net/http";"os")\nfunc main(){ http.ListenAndServe(":"+os.Getenv("PORT"), nil) }\n'},
         {"go"}, set(), {"PORT"}),
    Case("static-site",
         {"index.html": "<h1>hello</h1>", "style.css": "body{}"}, {"static"}),
    Case("fastapi-redis-celery-compose",
         {"docker-compose.yml": "services:\n  api:\n    build: .\n    ports: ['8000:8000']\n    environment:\n      REDIS_URL: redis://cache:6379/0\n  worker:\n    build: .\n    command: celery -A tasks worker\n    environment:\n      REDIS_URL: redis://cache:6379/0\n  cache:\n    image: redis:7\n",
          "Dockerfile": "FROM python:3.12\nEXPOSE 8000\nCMD [\"uvicorn\",\"main:app\"]\n", "requirements.txt": "fastapi\nuvicorn\ncelery\nredis\n"},
         {"docker"}, {"redis"}, {"REDIS_URL"}, set(), 2, "compose: decomposed without an LLM"),
    Case("compose-postgres-mongo",
         {"docker-compose.yml": "services:\n  web:\n    build: ./web\n    ports: ['3000:3000']\n    environment:\n      DATABASE_URL: postgres://app:pw@db:5432/app\n      MONGO_URL: mongodb://mongo:27017/logs\n  db:\n    image: postgres:16\n  mongo:\n    image: mongo:7\n",
          "web/Dockerfile": "FROM node:22\nEXPOSE 3000\n"},
         {"docker"}, {"postgres", "mongodb"}, {"DATABASE_URL", "MONGO_URL"}, set(), 1),
    Case("telegram-bot-python",
         {"requirements.txt": "python-telegram-bot\nrequests\n",
          "bot.py": "import os\nfrom telegram.ext import Application\nTOKEN=os.environ['BOT_TOKEN']\napp=Application.builder().token(TOKEN).build()\napp.run_polling()\n"},
         {"python"}, set(), {"BOT_TOKEN"}, {"foreign_service_dependency"}, 1, "background worker, foreign service"),
    Case("monorepo-react-express",
         {"frontend/package.json": '{"name":"fe","scripts":{"build":"vite build"},"dependencies":{"react":"18"},"devDependencies":{"vite":"5"}}', "frontend/index.html": "<div id=root></div>",
          "backend/package.json": PKG('"express":"^4","mongoose":"^8"'), "backend/server.js": "mongoose.connect(process.env.MONGODB_URI);app.listen(process.env.PORT)\n"},
         {"react", "node"}, {"mongodb"}, {"MONGODB_URI", "PORT"}, set(), 2, "two apps in one repo"),
    Case("committed-secrets-node",
         {"package.json": PKG('"express":"^4"'), "server.js": "const key='AKIAABCDEFGHIJKLMNOP';\nconst express=require('express');\n",
          ".env": "DB_PASSWORD=hunter2hunter2\nSTRIPE_KEY=sk_live_abcdefghijklmnopqrstuvwx\n"},
         {"node"}, set(), set(), {"hardcoded_secret", "env_file_present"}),
]
