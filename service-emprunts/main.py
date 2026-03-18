from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, Boolean, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timedelta
import httpx
import os
import time

# ── Application FastAPI ──────────────────────────────────
app = FastAPI(
    title="Service Emprunts — Bibliothèque DIT",
    description="Gestion des emprunts de livres",
    version="1.0.0"
)

# ── CORS ─────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Base de données ───────────────────────────────────────
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://admin:secret@db-emprunts:5432/emprunts_db"
)

# URLs des autres services
LIVRES_URL = os.environ.get("LIVRES_URL", "http://service-livres:8001")
USERS_URL  = os.environ.get("USERS_URL",  "http://service-users:8002")

engine       = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)
Base         = declarative_base()
###Block2#########################################################################""
# ── Modèle de la table emprunts ───────────────────────────
class Emprunt(Base):
    __tablename__ = "emprunts"

    id           = Column(Integer, primary_key=True, index=True)
    user_id      = Column(Integer, nullable=False)
    livre_id     = Column(Integer, nullable=False)
    date_emprunt = Column(DateTime, default=datetime.utcnow)
    date_retour  = Column(DateTime, nullable=True)
    retourne     = Column(Boolean, default=False)

    def to_dict(self):
        return {
            "id":           self.id,
            "user_id":      self.user_id,
            "livre_id":     self.livre_id,
            "date_emprunt": str(self.date_emprunt.date()),
            "date_retour":  str(self.date_retour.date()) if self.date_retour else None,
            "retourne":     self.retourne
        }

# ── Schémas Pydantic (validation des données) ─────────────
class EmpruntCreate(BaseModel):
    user_id:  int
    livre_id: int

class EmpruntRetour(BaseModel):
    emprunt_id: int

# ── Connexion BDD avec retry ──────────────────────────────
def init_db():
    retries = 5
    while retries > 0:
        try:
            Base.metadata.create_all(bind=engine)
            print("✅ Base de données emprunts connectée !")
            break
        except Exception as e:
            retries -= 1
            print(f"⏳ BDD pas encore prête... tentatives restantes : {retries}")
            time.sleep(3)

init_db()

# ── Dépendance session BDD ────────────────────────────────
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
### block3############################################################################# 
from fastapi import Depends
from sqlalchemy.orm import Session

# ─────────────────────────────────────────
# ROUTE 1 : Lister tous les emprunts
# GET /api/emprunts
# ─────────────────────────────────────────
@app.get("/api/emprunts")
def get_emprunts(db: Session = Depends(get_db)):
    emprunts = db.query(Emprunt).all()
    return [e.to_dict() for e in emprunts]


# ─────────────────────────────────────────
# ROUTE 2 : Emprunter un livre
# POST /api/emprunts
# Body : {"user_id": 1, "livre_id": 2}
# ─────────────────────────────────────────
@app.post("/api/emprunts", status_code=201)
async def emprunter(data: EmpruntCreate, db: Session = Depends(get_db)):

    # 1. Vérifier que le livre existe et est disponible
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{LIVRES_URL}/api/livres/{data.livre_id}")

    if r.status_code != 200:
        raise HTTPException(status_code=404, detail="Livre introuvable")

    livre = r.json()
    if not livre["disponible"]:
        raise HTTPException(status_code=400, detail="Livre déjà emprunté")

    # 2. Vérifier que l'utilisateur existe
    async with httpx.AsyncClient() as client:
        r2 = await client.get(f"{USERS_URL}/api/users/{data.user_id}")

    if r2.status_code != 200:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable")

    # 3. Créer l'emprunt (retour prévu dans 14 jours)
    date_retour = datetime.utcnow() + timedelta(days=14)
    emprunt = Emprunt(
        user_id      = data.user_id,
        livre_id     = data.livre_id,
        date_retour  = date_retour,
        retourne     = False
    )
    db.add(emprunt)
    db.commit()
    db.refresh(emprunt)

    # 4. Marquer le livre comme indisponible
    async with httpx.AsyncClient() as client:
        await client.put(
            f"{LIVRES_URL}/api/livres/{data.livre_id}",
            json={"disponible": False}
        )

    return {
        "message":      "Emprunt enregistré avec succès",
        "id":           emprunt.id,
        "retour_prevu": str(date_retour.date())
    }


# ─────────────────────────────────────────
# ROUTE 3 : Retourner un livre
# PUT /api/emprunts/{id}/retour
# ─────────────────────────────────────────
@app.put("/api/emprunts/{id}/retour")
async def retourner(id: int, db: Session = Depends(get_db)):

    emprunt = db.query(Emprunt).filter(Emprunt.id == id).first()
    if not emprunt:
        raise HTTPException(status_code=404, detail="Emprunt introuvable")

    if emprunt.retourne:
        raise HTTPException(status_code=400, detail="Livre déjà retourné")

    # Marquer comme retourné
    emprunt.retourne = True
    db.commit()

    # Remettre le livre disponible
    async with httpx.AsyncClient() as client:
        await client.put(
            f"{LIVRES_URL}/api/livres/{emprunt.livre_id}",
            json={"disponible": True}
        )

    return {"message": "Livre retourné avec succès"}


# ─────────────────────────────────────────
# ROUTE 4 : Historique d'un utilisateur
# GET /api/emprunts/historique/{user_id}
# ─────────────────────────────────────────
@app.get("/api/emprunts/historique/{user_id}")
def historique(user_id: int, db: Session = Depends(get_db)):
    emprunts = db.query(Emprunt).filter(
        Emprunt.user_id == user_id
    ).all()
    return [e.to_dict() for e in emprunts]
##BLOCK4#############################################################################
# ─────────────────────────────────────────
# ROUTE 5 : Détecter les retards
# GET /api/emprunts/retards
# ─────────────────────────────────────────
@app.get("/api/emprunts/retards")
def get_retards(db: Session = Depends(get_db)):
    maintenant = datetime.utcnow()

    retards = db.query(Emprunt).filter(
        Emprunt.retourne  == False,
        Emprunt.date_retour < maintenant
    ).all()

    return [
        {
            **e.to_dict(),
            "retard_jours": (maintenant - e.date_retour).days
        }
        for e in retards
    ]


# ─────────────────────────────────────────
# ROUTE 6 : Détail d'un emprunt
# GET /api/emprunts/{id}
# ─────────────────────────────────────────
@app.get("/api/emprunts/{id}")
def get_emprunt(id: int, db: Session = Depends(get_db)):
    emprunt = db.query(Emprunt).filter(Emprunt.id == id).first()
    if not emprunt:
        raise HTTPException(status_code=404, detail="Emprunt introuvable")
    return emprunt.to_dict()


# ─────────────────────────────────────────
# Page d'accueil — vérification service
# GET /
# ─────────────────────────────────────────
@app.get("/")
def root():
    return {
        "service":  "Service Emprunts",
        "version":  "1.0.0",
        "status":   "running",
        "docs":     "/docs"
    }
