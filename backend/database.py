# ============================================================
#  database.py  v7 — RunGroup + job versioning
# ============================================================

import json, datetime
from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Boolean,
    DateTime, Text, LargeBinary, ForeignKey, event, text
)
from sqlalchemy.orm import DeclarativeBase, relationship, sessionmaker

DB_URL = "sqlite:///./vrp_data.db"
engine = create_engine(DB_URL, connect_args={"check_same_thread": False}, echo=False)

@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cur = dbapi_connection.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

class Base(DeclarativeBase):
    pass

class RunGroup(Base):
    __tablename__ = "run_groups"
    id         = Column(String(36), primary_key=True)
    name       = Column(String(200), nullable=False)
    dataset_id = Column(Integer, ForeignKey("datasets.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    jobs = relationship("Job", back_populates="group", order_by="Job.created_at")
    def to_dict(self):
        return {"id":self.id,"name":self.name,"dataset_id":self.dataset_id,
                "created_at":self.created_at.isoformat() if self.created_at else None}

class Dataset(Base):
    __tablename__ = "datasets"
    id           = Column(Integer, primary_key=True, autoincrement=True)
    name         = Column(String(200), nullable=False)
    created_at   = Column(DateTime, default=datetime.datetime.utcnow)
    matrix_bytes = Column(LargeBinary, nullable=True)
    stores   = relationship("Store",   back_populates="dataset", cascade="all, delete-orphan")
    vehicles = relationship("Vehicle", back_populates="dataset", cascade="all, delete-orphan")
    jobs     = relationship("Job",     back_populates="dataset")

class Store(Base):
    __tablename__ = "stores"
    id=Column(Integer,primary_key=True,autoincrement=True); dataset_id=Column(Integer,ForeignKey("datasets.id"),nullable=False)
    store_id=Column(String(50)); node_id=Column(String(50)); eng_name=Column(String(200)); mn_name=Column(String(200))
    address=Column(String(400)); detail_addr=Column(String(400)); lat=Column(Float); lon=Column(Float)
    open_s=Column(Integer,default=0); close_s=Column(Integer,default=86399)
    dry_cbm=Column(Float,default=0.0); dry_kg=Column(Float,default=0.0)
    cold_cbm=Column(Float,default=0.0); cold_kg=Column(Float,default=0.0)
    has_dry=Column(Boolean,default=False); has_cold=Column(Boolean,default=False)
    dataset=relationship("Dataset",back_populates="stores")
    def to_dict(self): return {c.name:getattr(self,c.name) for c in self.__table__.columns}
    def to_solver_dict(self):
        return {"store_id":self.store_id,"node_id":self.node_id,"eng_name":self.eng_name or "",
                "mn_name":self.mn_name or "","address":self.address or "","detail_addr":self.detail_addr or "",
                "lat":self.lat,"lon":self.lon,"open_s":self.open_s,"close_s":self.close_s,
                "dry_cbm":self.dry_cbm,"dry_kg":self.dry_kg,"cold_cbm":self.cold_cbm,"cold_kg":self.cold_kg,
                "has_dry":self.has_dry,"has_cold":self.has_cold}

class Vehicle(Base):
    __tablename__ = "vehicles"
    id=Column(Integer,primary_key=True,autoincrement=True); dataset_id=Column(Integer,ForeignKey("datasets.id"),nullable=False)
    truck_id=Column(String(50)); description=Column(String(200)); depot=Column(String(100)); fleet=Column(String(10))
    cap_kg=Column(Float); cap_m3=Column(Float); fuel_cost_km=Column(Float); vehicle_cost=Column(Float); labor_cost=Column(Float)
    dataset=relationship("Dataset",back_populates="vehicles")
    def to_dict(self): return {c.name:getattr(self,c.name) for c in self.__table__.columns}
    def to_solver_dict(self):
        return {"truck_id":self.truck_id,"description":self.description or "","depot":self.depot,"fleet":self.fleet,
                "cap_kg":self.cap_kg,"cap_m3":self.cap_m3,"fuel_cost_km":self.fuel_cost_km,
                "vehicle_cost":self.vehicle_cost,"labor_cost":self.labor_cost}

class Job(Base):
    __tablename__ = "jobs"
    id                = Column(String(36), primary_key=True)
    dataset_id        = Column(Integer, ForeignKey("datasets.id"), nullable=True)
    group_id          = Column(String(36), ForeignKey("run_groups.id"), nullable=True)
    version_name      = Column(String(100), nullable=True)
    is_manual         = Column(Boolean, default=False)
    mode              = Column(String(20))
    max_trips         = Column(Integer)
    solver_time       = Column(Integer)
    rural_solver_time = Column(Integer, nullable=True)
    status            = Column(String(20), default="pending")
    error_msg         = Column(Text, nullable=True)
    created_at        = Column(DateTime, default=datetime.datetime.utcnow)
    completed_at      = Column(DateTime, nullable=True)
    dataset = relationship("Dataset", back_populates="jobs")
    group   = relationship("RunGroup", back_populates="jobs")
    result  = relationship("JobResult", back_populates="job", uselist=False, cascade="all, delete-orphan")
    def to_dict(self):
        return {"id":self.id,"dataset_id":self.dataset_id,"group_id":self.group_id,
                "version_name":self.version_name,"is_manual":bool(self.is_manual),
                "mode":self.mode,"max_trips":self.max_trips,"solver_time":self.solver_time,
                "rural_solver_time":self.rural_solver_time,"status":self.status,"error_msg":self.error_msg,
                "created_at":self.created_at.isoformat() if self.created_at else None,
                "completed_at":self.completed_at.isoformat() if self.completed_at else None}

class JobResult(Base):
    __tablename__ = "job_results"
    job_id        = Column(String(36), ForeignKey("jobs.id"), primary_key=True)
    summary_json  = Column(Text); routes_json=Column(Text); stops_json=Column(Text)
    unserved_json = Column(Text); map_data_json=Column(Text); excel_bytes=Column(LargeBinary)
    job=relationship("Job",back_populates="result")
    def get_summary(self):  return json.loads(self.summary_json  or "null")
    def get_routes(self):   return json.loads(self.routes_json   or "[]")
    def get_stops(self):    return json.loads(self.stops_json    or "[]")
    def get_unserved(self): return json.loads(self.unserved_json or "[]")
    def get_map_data(self): return json.loads(self.map_data_json or "[]")

def init_db():
    Base.metadata.create_all(engine)
    # Migrate existing DBs — ignore "duplicate column" errors
    with engine.connect() as conn:
        for sql in [
            "ALTER TABLE jobs ADD COLUMN group_id TEXT REFERENCES run_groups(id)",
            "ALTER TABLE jobs ADD COLUMN version_name TEXT",
            "ALTER TABLE jobs ADD COLUMN is_manual INTEGER DEFAULT 0",
        ]:
            try: conn.execute(text(sql)); conn.commit()
            except Exception: pass

def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()