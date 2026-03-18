# Image Python officielle légère
FROM python:3.11-slim

# Dossier de travail dans le conteneur
WORKDIR /app

# Copier requirements EN PREMIER (cache Docker)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copier tout le code source
COPY . .

# Déclarer le port
EXPOSE 8003

# Démarrer avec uvicorn
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8003", "--reload"]