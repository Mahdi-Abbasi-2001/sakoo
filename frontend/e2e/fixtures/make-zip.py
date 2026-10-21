import sys, zipfile
z = zipfile.ZipFile(sys.argv[1], 'w', zipfile.ZIP_DEFLATED)
z.writestr('docker-compose.yml', 'services:\n  web:\n    build: ./web\n    ports:\n      - "3000:3000"\n    environment:\n      PORT: "3000"\n')
z.writestr('web/Dockerfile', 'FROM node:20-alpine\nWORKDIR /app\nCOPY . .\nCMD ["node", "server.js"]\n')
z.writestr('web/server.js', 'require("http").createServer((q, r) => r.end("ok")).listen(3000);\n')
z.close()
