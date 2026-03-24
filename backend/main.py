# ============================================================
#  main.py  –  FastAPI + SQLite backend  v2
# ============================================================

import datetime, json, logging, os, uuid
from typing import Optional
import numpy as np


class _NumpySafeEncoder(json.JSONEncoder):
    """Converts numpy scalars to Python native types before JSON serialisation."""
    def default(self, obj):
        if isinstance(obj, np.integer):   return int(obj)
        if isinstance(obj, np.floating):  return float(obj)
        if isinstance(obj, np.bool_):     return bool(obj)
        if isinstance(obj, np.ndarray):   return obj.tolist()
        return super().default(obj)


def _dumps(obj) -> str:
    return json.dumps(obj, cls=_NumpySafeEncoder)

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, FileResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel

import config, data_loader
import solver as vrp_solver
import output_formatter, osrm_client
from database import init_db, get_db, Dataset, Store, Vehicle, Job, JobResult

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-8s  %(message)s")
log = logging.getLogger(__name__)

init_db()

app = FastAPI(title="VRP Route Optimization System", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])


# ════════════════════════════════════════════════════════════
#  Pydantic schemas
# ════════════════════════════════════════════════════════════

class StoreUpdate(BaseModel):
    eng_name   : Optional[str]   = None
    mn_name    : Optional[str]   = None
    address    : Optional[str]   = None
    detail_addr: Optional[str]   = None
    lat        : Optional[float] = None
    lon        : Optional[float] = None
    open_s     : Optional[int]   = None
    close_s    : Optional[int]   = None
    dry_cbm    : Optional[float] = None
    dry_kg     : Optional[float] = None
    cold_cbm   : Optional[float] = None
    cold_kg    : Optional[float] = None

class StoreCreate(BaseModel):
    store_id   : str
    eng_name   : str   = ""
    mn_name    : str   = ""
    address    : str   = ""
    detail_addr: str   = ""
    lat        : float
    lon        : float
    open_s     : int   = 0
    close_s    : int   = 86399
    dry_cbm    : float = 0.0
    dry_kg     : float = 0.0
    cold_cbm   : float = 0.0
    cold_kg    : float = 0.0

class VehicleUpdate(BaseModel):
    description  : Optional[str]   = None
    depot        : Optional[str]   = None
    cap_kg       : Optional[float] = None
    cap_m3       : Optional[float] = None
    fuel_cost_km : Optional[float] = None
    vehicle_cost : Optional[float] = None
    labor_cost   : Optional[float] = None

class VehicleCreate(BaseModel):
    truck_id     : str
    description  : str   = ""
    depot        : str
    cap_kg       : float
    cap_m3       : float
    fuel_cost_km : float = 0.0
    vehicle_cost : float = 0.0
    labor_cost   : float = 0.0


# ════════════════════════════════════════════════════════════
#  Health
# ════════════════════════════════════════════════════════════

@app.get("/api/health")
def health():
    osrm_ok = False
    try:
        import requests as rq
        r = rq.get(f"{config.OSRM_URL}/route/v1/driving/106.9,47.9;106.9,47.91",
                   timeout=3)
        osrm_ok = r.status_code == 200
    except Exception:
        pass
    return {"status": "ok", "osrm": "connected" if osrm_ok else "unreachable",
            "osrm_url": config.OSRM_URL, "version": "2.0.0"}


# ════════════════════════════════════════════════════════════
#  Dataset CRUD
# ════════════════════════════════════════════════════════════

@app.get("/api/datasets")
def list_datasets(db: Session = Depends(get_db)):
    rows = db.query(Dataset).order_by(Dataset.created_at.desc()).all()
    return [{"id": d.id, "name": d.name,
             "created_at"   : d.created_at.isoformat(),
             "store_count"  : len(d.stores),
             "vehicle_count": len(d.vehicles),
             "has_matrix"   : d.matrix_bytes is not None}
            for d in rows]


@app.post("/api/datasets")
async def create_dataset(
    name        : str                    = Form(...),
    store_file  : UploadFile             = File(...),
    matrix_file : Optional[UploadFile]   = File(None),
    db          : Session                = Depends(get_db),
):
    store_bytes  = await store_file.read()
    matrix_bytes = await matrix_file.read() if matrix_file else None

    try:
        stores_list   = data_loader.load_stores(store_bytes)
        vehicles_list = data_loader.load_vehicles(store_bytes)
    except Exception as e:
        raise HTTPException(422, f"Parse error: {e}")

    ds = Dataset(name=name, matrix_bytes=matrix_bytes)
    db.add(ds)
    db.flush()

    for s in stores_list:
        db.add(Store(dataset_id=ds.id, **{
            k: s[k] for k in ["store_id","node_id","eng_name","mn_name",
                               "address","detail_addr","lat","lon","open_s",
                               "close_s","dry_cbm","dry_kg","cold_cbm",
                               "cold_kg","has_dry","has_cold"]
        }))
    for v in vehicles_list:
        db.add(Vehicle(dataset_id=ds.id, **{
            k: v[k] for k in ["truck_id","description","depot","fleet",
                               "cap_kg","cap_m3","fuel_cost_km",
                               "vehicle_cost","labor_cost"]
        }))

    db.commit()
    return {"id": ds.id, "name": ds.name,
            "store_count": len(stores_list),
            "vehicle_count": len(vehicles_list)}


@app.delete("/api/datasets/{dataset_id}")
def delete_dataset(dataset_id: int, db: Session = Depends(get_db)):
    ds = db.get(Dataset, dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")
    db.delete(ds)
    db.commit()
    return {"ok": True}


@app.post("/api/datasets/{dataset_id}/matrix")
async def upload_matrix_to_dataset(
    dataset_id  : int,
    matrix_file : UploadFile = File(...),
    db          : Session    = Depends(get_db),
):
    ds = db.get(Dataset, dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")
    ds.matrix_bytes = await matrix_file.read()
    db.commit()
    return {"ok": True, "dataset_id": dataset_id}


# ════════════════════════════════════════════════════════════
#  Store CRUD
# ════════════════════════════════════════════════════════════

@app.get("/api/datasets/{dataset_id}/stores")
def list_stores(dataset_id: int, db: Session = Depends(get_db)):
    return [s.to_dict() for s in
            db.query(Store).filter(Store.dataset_id == dataset_id).all()]


@app.post("/api/datasets/{dataset_id}/stores")
def add_store(dataset_id: int, body: StoreCreate, db: Session = Depends(get_db)):
    if not db.get(Dataset, dataset_id):
        raise HTTPException(404, "Dataset not found")
    def norm(x):
        try: return str(int(str(x).strip()))
        except: return str(x).strip()
    s = Store(
        dataset_id=dataset_id, store_id=body.store_id,
        node_id=norm(body.store_id), eng_name=body.eng_name,
        mn_name=body.mn_name, address=body.address,
        detail_addr=body.detail_addr, lat=body.lat, lon=body.lon,
        open_s=body.open_s, close_s=body.close_s,
        dry_cbm=body.dry_cbm, dry_kg=body.dry_kg,
        cold_cbm=body.cold_cbm, cold_kg=body.cold_kg,
        has_dry=body.dry_kg>0 or body.dry_cbm>0,
        has_cold=body.cold_kg>0 or body.cold_cbm>0,
    )
    db.add(s); db.commit(); db.refresh(s)
    return s.to_dict()


@app.put("/api/datasets/{dataset_id}/stores/{sid}")
def update_store(dataset_id: int, sid: int, body: StoreUpdate,
                 db: Session = Depends(get_db)):
    s = db.query(Store).filter(Store.id==sid, Store.dataset_id==dataset_id).first()
    if not s:
        raise HTTPException(404, "Store not found")
    for f, v in body.model_dump(exclude_none=True).items():
        setattr(s, f, v)
    s.has_dry  = (s.dry_kg or 0)>0 or (s.dry_cbm or 0)>0
    s.has_cold = (s.cold_kg or 0)>0 or (s.cold_cbm or 0)>0
    db.commit(); db.refresh(s)
    return s.to_dict()


@app.delete("/api/datasets/{dataset_id}/stores/{sid}")
def delete_store(dataset_id: int, sid: int, db: Session = Depends(get_db)):
    s = db.query(Store).filter(Store.id==sid, Store.dataset_id==dataset_id).first()
    if not s:
        raise HTTPException(404, "Store not found")
    db.delete(s); db.commit()
    return {"ok": True}


@app.delete("/api/datasets/{dataset_id}/stores")
def clear_stores(dataset_id: int, db: Session = Depends(get_db)):
    db.query(Store).filter(Store.dataset_id==dataset_id).delete()
    db.commit()
    return {"ok": True}


# ════════════════════════════════════════════════════════════
#  Vehicle CRUD
# ════════════════════════════════════════════════════════════

@app.get("/api/datasets/{dataset_id}/vehicles")
def list_vehicles(dataset_id: int, db: Session = Depends(get_db)):
    return [v.to_dict() for v in
            db.query(Vehicle).filter(Vehicle.dataset_id==dataset_id).all()]


@app.post("/api/datasets/{dataset_id}/vehicles")
def add_vehicle(dataset_id: int, body: VehicleCreate, db: Session = Depends(get_db)):
    if not db.get(Dataset, dataset_id):
        raise HTTPException(404, "Dataset not found")
    fleet = config.DEPOT_VEHICLE_MAP.get(body.depot, "DRY")
    v = Vehicle(
        dataset_id=dataset_id, truck_id=body.truck_id,
        description=body.description, depot=body.depot, fleet=fleet,
        cap_kg=body.cap_kg, cap_m3=body.cap_m3,
        fuel_cost_km=body.fuel_cost_km,
        vehicle_cost=body.vehicle_cost, labor_cost=body.labor_cost,
    )
    db.add(v); db.commit(); db.refresh(v)
    return v.to_dict()


@app.put("/api/datasets/{dataset_id}/vehicles/{vid}")
def update_vehicle(dataset_id: int, vid: int, body: VehicleUpdate,
                   db: Session = Depends(get_db)):
    v = db.query(Vehicle).filter(Vehicle.id==vid, Vehicle.dataset_id==dataset_id).first()
    if not v:
        raise HTTPException(404, "Vehicle not found")
    for f, val in body.model_dump(exclude_none=True).items():
        setattr(v, f, val)
    if v.depot:
        v.fleet = config.DEPOT_VEHICLE_MAP.get(v.depot, "DRY")
    db.commit(); db.refresh(v)
    return v.to_dict()


@app.delete("/api/datasets/{dataset_id}/vehicles/{vid}")
def delete_vehicle(dataset_id: int, vid: int, db: Session = Depends(get_db)):
    v = db.query(Vehicle).filter(Vehicle.id==vid, Vehicle.dataset_id==dataset_id).first()
    if not v:
        raise HTTPException(404, "Vehicle not found")
    db.delete(v); db.commit()
    return {"ok": True}


@app.delete("/api/datasets/{dataset_id}/vehicles")
def clear_vehicles(dataset_id: int, db: Session = Depends(get_db)):
    db.query(Vehicle).filter(Vehicle.dataset_id==dataset_id).delete()
    db.commit()
    return {"ok": True}


# ════════════════════════════════════════════════════════════
#  Optimize  (DB-backed — results never expire)
# ════════════════════════════════════════════════════════════

@app.post("/api/optimize")
async def optimize(
    dataset_id      : Optional[int]          = Form(None),
    store_file      : Optional[UploadFile]   = File(None),
    matrix_file     : Optional[UploadFile]   = File(None),
    mode            : str                    = Form("cheapest"),
    max_trips       : int                    = Form(3),
    solver_time     : int                    = Form(120),
    rural_solver_time : Optional[int]        = Form(None),
    db              : Session                = Depends(get_db),
):
    if mode not in ("fastest","shortest","cheapest"):
        raise HTTPException(400, f"Invalid mode '{mode}'")

    # ── Resolve data ─────────────────────────────────────────
    if dataset_id:
        ds = db.get(Dataset, dataset_id)
        if not ds:
            raise HTTPException(404, "Dataset not found")
        stores_list   = [s.to_solver_dict() for s in ds.stores]
        vehicles_list = [v.to_solver_dict() for v in ds.vehicles]
        matrix_bytes  = ds.matrix_bytes
        if not matrix_bytes:
            raise HTTPException(422, "No matrix for this dataset — upload it first")
    elif store_file and matrix_file:
        sb = await store_file.read()
        mb = await matrix_file.read()
        stores_list   = data_loader.load_stores(sb)
        vehicles_list = data_loader.load_vehicles(sb)
        matrix_bytes  = mb
        dataset_id    = None
    else:
        raise HTTPException(422, "Provide dataset_id OR both store_file+matrix_file")

    try:
        dist_df, dur_df = data_loader.load_matrix(matrix_bytes)
    except Exception as e:
        raise HTTPException(422, f"Matrix error: {e}")

    warnings = data_loader.validate_data(stores_list, vehicles_list, dist_df, dur_df)

    # ── Create Job record ─────────────────────────────────────
    job_id = str(uuid.uuid4())
    job    = Job(id=job_id, dataset_id=dataset_id, mode=mode,
                 max_trips=max_trips, solver_time=solver_time, 
                 rural_solver_time=rural_solver_time,
                 status="running")
    db.add(job); db.commit()

    # ── Solve ─────────────────────────────────────────────────
    config.MAX_TRIPS_PER_VEHICLE   = max_trips
    config.MAX_SOLVER_TIME_SECONDS = solver_time
    
    # Set rural solver time if provided, otherwise use default
    if rural_solver_time is not None:
        config.RURAL_SOLVER_TIME_SECONDS = rural_solver_time

    try:
        result = vrp_solver.solve(stores_list, vehicles_list, dist_df, dur_df, mode=mode)
    except Exception as e:
        job.status = "error"; job.error_msg = str(e); db.commit()
        raise HTTPException(500, f"Solver error: {e}")

    # ── OSRM geometries ───────────────────────────────────────
    route_geometries = {}
    try:
        wps_map = {}
        for fleet, fr in result.items():
            for route in fr["routes"]:
                dc  = config.DEPOTS["Dry DC"] if fleet=="DRY" else config.DEPOTS["Cold DC"]
                wps = [(dc["lat"], dc["lon"])]
                for stop in route["stops"]:
                    wps.append((stop["lat"], stop["lon"]))
                wps.append((dc["lat"], dc["lon"]))
                wps_map[route["virtual_id"]] = wps
        route_geometries = osrm_client.get_route_geometries_batch(wps_map)
    except Exception as e:
        log.warning(f"OSRM geometry skipped: {e}")

    # ── Outputs ───────────────────────────────────────────────
    route_summary = output_formatter.build_route_summary(result)
    stop_details  = output_formatter.build_stop_details(result)
    unserved      = output_formatter.build_unserved(result, dist_df)
    map_data      = output_formatter.build_map_data(result, route_geometries)

    served       = sum(len(r["stops"]) for fr in result.values() for r in fr["routes"])
    unserved_n   = sum(len(fr["unserved"]) for fr in result.values())
    total_cost   = sum(r["cost_total"]  for r in route_summary)
    total_dist   = sum(r["distance_km"] for r in route_summary)
    # Man-hours: total driver-hours consumed across all trips.
    # Each trip's man-hours = (return_time_s - start_offset_s) / 3600.
    # Summed across all trips gives total person-hours dispatched this shift.
    total_man_hours = round(sum(r.get("man_hours", 0) for r in route_summary), 1)

    summary = {
        "mode": mode, "total_stores": len(stores_list),
        "total_served": served, "total_unserved": unserved_n,
        "total_routes": len(route_summary),
        "total_dist_km": round(total_dist, 1),
        "total_cost": round(total_cost, 0),
        "total_man_hours": total_man_hours,
        "warnings": warnings,
    }

    excel_bytes = output_formatter.export_to_excel(route_summary, stop_details, unserved)

    db.add(JobResult(
        job_id        = job_id,
        summary_json  = _dumps(summary),
        routes_json   = _dumps(route_summary),
        stops_json    = _dumps(stop_details),
        unserved_json = _dumps(unserved),
        map_data_json = _dumps(map_data),
        excel_bytes   = excel_bytes,
    ))
    job.status       = "done"
    job.completed_at = datetime.datetime.utcnow()
    db.commit()

    log.info(f"Job {job_id[:8]} done — {served} served, {unserved_n} unserved, mode={mode}")

    return {"job_id": job_id, "summary": summary,
            "route_summary": route_summary, "stop_details": stop_details,
            "unserved": unserved, "map_data": map_data}


# ════════════════════════════════════════════════════════════
#  Job history & result retrieval
# ════════════════════════════════════════════════════════════

@app.get("/api/jobs")
def list_jobs(limit: int = 30, db: Session = Depends(get_db)):
    jobs = db.query(Job).order_by(Job.created_at.desc()).limit(limit).all()
    out  = []
    for j in jobs:
        d = j.to_dict()
        if j.result:
            s = j.result.get_summary()
            d["total_served"]   = s.get("total_served")   if s else None
            d["total_unserved"] = s.get("total_unserved") if s else None
            d["total_routes"]   = s.get("total_routes")   if s else None
            d["total_cost"]     = s.get("total_cost")     if s else None
        out.append(d)
    return out


@app.get("/api/jobs/{job_id}")
def get_job_result(job_id: str, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    info = job.to_dict()
    if job.result:
        info.update({
            "summary"      : job.result.get_summary(),
            "route_summary": job.result.get_routes(),
            "stop_details" : job.result.get_stops(),
            "unserved"     : job.result.get_unserved(),
            "map_data"     : job.result.get_map_data(),
        })
    return info


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    db.delete(job); db.commit()
    return {"ok": True}


# ════════════════════════════════════════════════════════════
#  Excel export  (persistent — no expiry)
# ════════════════════════════════════════════════════════════

@app.get("/api/export/{job_id}")
def export_excel(job_id: str, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job or not job.result or not job.result.excel_bytes:
        raise HTTPException(404, "Job result not found. Run optimization first.")
    return Response(
        content = job.result.excel_bytes,
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers = {"Content-Disposition":
                   f'attachment; filename="vrp_{job_id[:8]}_{job.mode}.xlsx"'},
    )


# ════════════════════════════════════════════════════════════
#  Build Matrix
# ════════════════════════════════════════════════════════════

@app.post("/api/build-matrix")
async def build_matrix_endpoint(
    dataset_id      : Optional[int]          = Form(None),
    store_file      : Optional[UploadFile]   = File(None),
    matrix_file     : Optional[UploadFile]   = File(None),
    save_to_dataset : bool                   = Form(True),
    db              : Session                = Depends(get_db),
):
    if dataset_id:
        ds          = db.get(Dataset, dataset_id)
        if not ds:
            raise HTTPException(404, "Dataset not found")
        stores_list = [s.to_solver_dict() for s in ds.stores]
    elif store_file:
        stores_list = data_loader.load_stores(await store_file.read())
        dataset_id  = None
    else:
        raise HTTPException(422, "Provide dataset_id or store_file")

    # If matrix file is uploaded, use it directly
    if matrix_file:
        matrix_bytes = await matrix_file.read()
        
        # Validate matrix file format
        try:
            import pandas as pd, io as _io
            df = pd.read_excel(_io.BytesIO(matrix_bytes), sheet_name=None)
            if config.DURATION_SHEET not in df or config.DISTANCE_SHEET not in df:
                raise HTTPException(400, f"Matrix file must contain '{config.DURATION_SHEET}' and '{config.DISTANCE_SHEET}' sheets")
        except Exception as e:
            raise HTTPException(400, f"Invalid matrix file: {str(e)}")
        
        # Save to dataset if requested
        if dataset_id and save_to_dataset:
            ds = db.get(Dataset, dataset_id)
            if ds:
                ds.matrix_bytes = matrix_bytes
                db.commit()
        
        return Response(
            content    = matrix_bytes,
            media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers    = {"Content-Disposition": 'attachment; filename="matrix.xlsx"'},
        )
    
    # Otherwise, build matrix via OSRM
    coords, ids = [], []
    for dc_name, dc in config.DEPOTS.items():
        coords.append((dc["lat"], dc["lon"])); ids.append(dc_name)
    for s in stores_list:
        coords.append((s["lat"], s["lon"])); ids.append(s["node_id"])

    try:
        dist_mat, dur_mat = osrm_client.build_matrix_from_osrm(coords)
    except ConnectionError as e:
        raise HTTPException(503, str(e))

    import pandas as pd, io as _io
    buf = _io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        pd.DataFrame((dur_mat/60).round(2), index=ids, columns=ids).to_excel(
            writer, sheet_name=config.DURATION_SHEET)
        pd.DataFrame(dist_mat, index=ids, columns=ids).to_excel(
            writer, sheet_name=config.DISTANCE_SHEET)
    buf.seek(0)
    matrix_bytes = buf.read()

    if dataset_id and save_to_dataset:
        ds = db.get(Dataset, dataset_id)
        if ds:
            ds.matrix_bytes = matrix_bytes
            db.commit()

    return Response(
        content    = matrix_bytes,
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers    = {"Content-Disposition": 'attachment; filename="matrix.xlsx"'},
    )


# ════════════════════════════════════════════════════════════
#  Frontend
# ════════════════════════════════════════════════════════════

@app.get("/")
def serve_frontend():
    if os.path.exists("index.html"):
        return FileResponse("index.html")
    return JSONResponse({"message": "VRP API v2 — see /docs"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)