"""
ULTIMATE TREASURE FINDER - NIR Edition v4.2.1
==============================================

Archaeological anomaly detection using multi-source remote sensing:
- LIDAR: Terrain anomalies (buried structures)
- Sentinel-2 NIR: Vegetation indices (crop marks)
- Historical Maps: Known structures and paths
- Multi-temporal Analysis: 3-year persistence

VERSION HISTORY:
- v4.2.1 (2026-01-05): Hotspot Detection Sensitivity Improvements
  * Reduced clustering thresholds (93-97% → 85-92%) for more candidates
  * Automatic percentile fallback if candidate pixels are too scarce (<0.02%)
  * Reduced DBSCAN min_samples (5 → 3) for smaller feature detection
  * Relaxed invalid hotspot filters (NDVI: -0.2→-0.3, 0.85→0.90; NDWI: 0.7→0.8)
  * Reduced base confidence threshold (0.15 → 0.10)
  * Reduced aerial analysis percentile (85% → 80%)
  * Reduced LIDAR analysis thresholds (85-90% → 75-85%)
  * Enhanced logging with detailed statistics
  * Expected: 10-30 hotspots instead of 1-2
  * See HOTSPOT_DETECTION_FIXES_v4.2.1.md for details

- v4.2.0 (2026-01-04): Scientific Perfection
  * Fixed NDWI threshold (0.9 → 0.7)
  * Added NDVI range checks for seasonal contrast (0.2-0.5)
  * Removed duplicate function calls
  * Enhanced NIR index documentation
  * See AENDERUNGSPROTOKOLL.md for details

SCIENTIFIC FOUNDATION:
- Tucker (1979): NDVI
- Huete (1988): SAVI (key for archaeology!)
- Huete et al. (2002): EVI
- McFeeters (1996): NDWI
- Woebbecke et al. (1995): ExG

USAGE:
  python treasure_finder_ultimate_gee_v4_2_1_COMPLETE.py \\
    --lidar neuLIDAR.png --lidar-world neuLIDAR.pgw \\
    --use-gee --gee-project YOUR_PROJECT_ID \\
    --multi-temporal ultimate
"""

import numpy as np
from PIL import Image
import xml.etree.ElementTree as ET
from xml.dom import minidom
import cv2
from scipy import ndimage
from skimage.feature import graycomatrix, graycoprops
from sklearn.cluster import DBSCAN
from typing import List, Tuple, Dict, Optional
import os
import sys
import argparse
import logging
from pathlib import Path
from datetime import datetime, timedelta
import math

# Google Earth Engine Import
try:
    import ee
    GEE_AVAILABLE = True
except ImportError:
    GEE_AVAILABLE = False


def expand_bbox_wgs84_m(bbox_wgs84: Tuple[float, float, float, float], buffer_m: float) -> Tuple[float, float, float, float]:
    """
    Erweitert eine BBox (min_lon, min_lat, max_lon, max_lat) um einen Buffer in Metern.
    
    *** WICHTIG: Gibt mehr Kontext für Crop Mark Detection! ***
    
    Args:
        bbox_wgs84: (min_lon, min_lat, max_lon, max_lat) in WGS84
        buffer_m: Buffer in Metern (Standard: 500m für archäologischen Kontext)
    
    Returns:
        Erweiterte BBox (min_lon, min_lat, max_lon, max_lat)
    """
    min_lon, min_lat, max_lon, max_lat = bbox_wgs84
    
    if buffer_m is None or buffer_m <= 0:
        return bbox_wgs84
    
    # Berechne Meter pro Grad
    mid_lat = (min_lat + max_lat) * 0.5
    m_per_deg_lat = 111320.0  # Konstant: ~111km pro Grad Latitude
    m_per_deg_lon = 111320.0 * max(0.1, math.cos(math.radians(mid_lat)))  # Abhängig von Latitude
    
    # Konvertiere Buffer von Metern zu Grad
    dlat = float(buffer_m) / m_per_deg_lat
    dlon = float(buffer_m) / m_per_deg_lon
    
    # Erweitere BBox in alle Richtungen
    return (
        min_lon - dlon,
        min_lat - dlat,
        max_lon + dlon,
        max_lat + dlat
    )


class GoogleEarthEngineConnector:
    """
    Connector für automatischen Download von Sentinel-2 Daten.
    """
    
    def __init__(self, project_id: str, logger=None):
        self.logger = logger or logging.getLogger(__name__)
        self.project_id = project_id
        
        if not GEE_AVAILABLE:
            self.logger.error("❌ earthengine-api nicht installiert!")
            self.logger.error("Installation: pip install earthengine-api")
            self.initialized = False
            return
        
        try:
            ee.Initialize(project=project_id)
            self.initialized = True
            self.logger.info(f"✅ Google Earth Engine initialisiert (Project: {project_id})")
        except Exception as e:
            self.logger.error(f"❌ GEE Initialisierung fehlgeschlagen: {e}")
            self.logger.error("   Führe aus: python setup_gee_interactive.py")
            self.initialized = False
    
    def _ndvi_to_rgb(self, ndvi: np.ndarray) -> np.ndarray:
        """
        Map NDVI float array to RGB uint8 for visual debug.
        *** ÜBERNOMMEN vom funktionierenden Code ***
        Color scheme: Blue -> Green -> Yellow -> Red
        Range: -0.2 (blue) to 0.9 (red)
        """
        x = ndvi.astype("float32")
        x = np.clip(x, -0.2, 0.9)
        x = (x + 0.2) / 1.1  # Normalisiere -0.2...0.9 auf 0...1
        x = np.clip(x, 0.0, 1.0)
        rgb = np.zeros((x.shape[0], x.shape[1], 3), dtype="float32")
        
        # Phase 1: 0.0 - 0.5 → Blue to Green
        m1 = x <= 0.5
        t1 = np.zeros_like(x)
        t1[m1] = x[m1] / 0.5
        rgb[m1, 1] = 255.0 * t1[m1]      # Green steigt
        rgb[m1, 2] = 255.0 * (1.0 - t1[m1])  # Blue fällt
        
        # Phase 2: 0.5 - 0.75 → Green to Yellow
        m2 = (x > 0.5) & (x <= 0.75)
        t2 = (x[m2] - 0.5) / 0.25
        rgb[m2, 0] = 255.0 * t2  # Red steigt
        rgb[m2, 1] = 255.0       # Green bleibt
        
        # Phase 3: 0.75 - 1.0 → Yellow to Red
        m3 = x > 0.75
        t3 = (x[m3] - 0.75) / 0.25
        rgb[m3, 0] = 255.0            # Red bleibt
        rgb[m3, 1] = 255.0 * (1.0 - t3)  # Green fällt
        
        return np.clip(rgb, 0, 255).astype("uint8")
    
    def download_nir_data(self, 
                         bbox: Tuple[float, float, float, float],
                         resolution: int = 10,
                         time_range: Tuple[str, str] = None,
                         max_cloud_coverage: float = 20.0,
                         multi_temporal: str = 'ultimate') -> Optional[Dict[str, np.ndarray]]:
        """
        Lädt Sentinel-2 NIR und RGB Daten von Google Earth Engine.
        
        *** MULTI-TEMPORAL MODES: ***
        - 'off': Single-date (schnell, 60 Tage)
        - 'seasonal': Beste Crop Mark Saison (Juli-Aug)
        - 'ultimate': 3 Jahre multi-temporal mit Persistenz-Analyse (MAXIMUM PRECISION!)
        
        Args:
            bbox: (min_lon, min_lat, max_lon, max_lat) in WGS84
            resolution: Auflösung in Metern (10, 20, oder 60)
            time_range: (start_date, end_date) - wird bei multi_temporal ignoriert
            max_cloud_coverage: Maximale Wolkenbedeckung in Prozent (0-100)
            multi_temporal: 'off', 'seasonal', oder 'ultimate'
        
        Returns:
            Dict mit 'red', 'green', 'blue', 'nir' Arrays + Multi-Temporal Metriken
        """
        if multi_temporal == 'ultimate':
            return self._download_nir_multitemporal_ultimate(bbox, resolution, max_cloud_coverage)
        elif multi_temporal == 'seasonal':
            return self._download_nir_seasonal(bbox, resolution, max_cloud_coverage)
        else:
            return self._download_nir_single_date(bbox, resolution, time_range, max_cloud_coverage)
    
    def _download_nir_single_date(self,
                                   bbox: Tuple[float, float, float, float],
                                   resolution: int = 10,
                                   time_range: Tuple[str, str] = None,
                                   max_cloud_coverage: float = 20.0) -> Optional[Dict[str, np.ndarray]]:
        """
        SINGLE-DATE Mode: Lädt beste Szene der letzten 60 Tage.
        Schnell, aber weniger präzise.
        """
        if not self.initialized:
            self.logger.error("Google Earth Engine nicht initialisiert")
            return None
        
        try:
            import rasterio
            import zipfile
            import tempfile
            import shutil
            import requests
            from pathlib import Path
        except ImportError as e:
            self.logger.error(f"Benötigte Library fehlt: {e}")
            return None
        
        try:
            # Zeitraum
            if time_range is None:
                end_date = datetime.now()
                start_date = end_date - timedelta(days=60)
                time_range = (
                    start_date.strftime("%Y-%m-%d"),
                    end_date.strftime("%Y-%m-%d")
                )
            
            min_lon, min_lat, max_lon, max_lat = bbox
            
            self.logger.info(f"🌍 Lade Sentinel-2 von Google Earth Engine...")
            self.logger.info(f"   BBox: {min_lon:.6f},{min_lat:.6f} .. {max_lon:.6f},{max_lat:.6f}")
            self.logger.info(f"   Zeitraum: {time_range[0]} bis {time_range[1]}")
            self.logger.info(f"   Max Wolken: {max_cloud_coverage}%")
            self.logger.info(f"   Auflösung: {resolution}m")
            
            # Region definieren
            region = ee.Geometry.Rectangle([min_lon, min_lat, max_lon, max_lat], 
                                          proj='EPSG:4326', geodesic=False)
            
            # Sentinel-2 Collection
            # *** WICHTIG: SCL-basiertes Cloud-Masking (besser als QA60!) ***
            def mask_s2_clouds(img):
                """Maskiert Wolken mit Scene Classification Layer (SCL)"""
                scl = img.select('SCL')
                # Maskiere: 3=Cloud shadows, 8/9=Clouds, 10=Cirrus, 11=Snow
                mask = (scl.neq(3).And(scl.neq(8)).And(scl.neq(9))
                       .And(scl.neq(10)).And(scl.neq(11)))
                return img.updateMask(mask)
            
            collection = (ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
                         .filterBounds(region)
                         .filterDate(time_range[0], time_range[1])
                         .filter(ee.Filter.lte('CLOUDY_PIXEL_PERCENTAGE', max_cloud_coverage)))
            
            # Cloud-Masking anwenden (mit try/except falls SCL nicht verfügbar)
            try:
                collection = collection.map(mask_s2_clouds)
                self.logger.info("   ✅ SCL Cloud-Masking aktiv")
            except Exception:
                self.logger.warning("   ⚠️ SCL nicht verfügbar, nutze nur Metadata-Filter")
            
            count = collection.size().getInfo()
            self.logger.info(f"   Gefunden: {count} Sentinel-2 Szenen")
            
            if count == 0:
                self.logger.warning("⚠️ Keine Bilder gefunden!")
                return None
            
            # *** WICHTIG: Median-Composite für Robustheit ***
            self.logger.info(f"   📊 Erstelle Median-Composite aus max. 10 Szenen")
            image = collection.limit(10).median()
            
            # Bänder: B4=Red, B3=Green, B2=Blue, B8=NIR
            image = image.select(['B4', 'B3', 'B2', 'B8'])
            
            # *** WICHTIG: scale statt dimensions ***
            download_params = {
                'scale': int(resolution),
                'crs': 'EPSG:4326',
                'region': region,
                'format': 'GEO_TIFF',
                'filePerBand': False
            }
            
            self.logger.info("   ⏳ Downloade Daten (kann 30-90 Sekunden dauern)...")
            
            # *** BUGFIX #1: Retry-Logik bei Rate Limits ***
            max_retries = 3
            retry_delay = 5
            url = None
            response = None
            
            for attempt in range(max_retries):
                try:
                    url = image.getDownloadURL(download_params)
                    
                    # *** KRITISCH: Stream-Download für große Dateien ***
                    response = requests.get(url, stream=True, timeout=600)
                    response.raise_for_status()
                    break  # Success!
                    
                except (requests.exceptions.RequestException, Exception) as e:
                    if attempt < max_retries - 1:
                        self.logger.warning(f"⚠️ Download fehlgeschlagen (Versuch {attempt + 1}/{max_retries}): {e}")
                        self.logger.info(f"   Warte {retry_delay}s vor erneutem Versuch...")
                        import time
                        time.sleep(retry_delay)
                        retry_delay *= 2  # Exponential backoff
                    else:
                        self.logger.error(f"❌ Download nach {max_retries} Versuchen fehlgeschlagen: {e}")
                        return None
            
            if response is None:
                self.logger.error("❌ Download fehlgeschlagen!")
                return None
            
            # Temporäres Verzeichnis
            tmpdir = tempfile.mkdtemp(prefix='gee_s2_')
            out_path = Path(tmpdir) / 'gee_download.bin'
            
            # Download
            with open(out_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=1024*1024):
                    if chunk:
                        f.write(chunk)
            
            self.logger.info(f"   📦 Download abgeschlossen ({out_path.stat().st_size / 1024 / 1024:.1f} MB)")
            
            # *** KRITISCH: GEE kann ZIP oder direktes GeoTIFF zurückgeben! ***
            tif_path = None
            
            # 1) Ist es ein ZIP?
            if zipfile.is_zipfile(out_path):
                self.logger.info("   📂 Entpacke ZIP...")
                with zipfile.ZipFile(out_path, 'r') as zf:
                    zf.extractall(tmpdir)
                # Suche .tif Datei
                for p in Path(tmpdir).rglob('*.tif'):
                    tif_path = p
                    break
            else:
                # 2) Ist es direktes TIFF?
                head = out_path.read_bytes()[:4]
                is_tif = head in (b'II*\x00', b'MM\x00*')
                if is_tif:
                    tif_path = Path(tmpdir) / 'gee_rgbnir.tif'
                    out_path.rename(tif_path)
                    self.logger.info("   ✅ Direktes GeoTIFF erkannt")
                else:
                    self.logger.error(f"   ❌ Unerwarteter Payload-Typ")
                    # Letzte Chance: suche .tif
                    for p in Path(tmpdir).rglob('*.tif'):
                        tif_path = p
                        break
            
            if tif_path is None or not tif_path.exists():
                shutil.rmtree(tmpdir, ignore_errors=True)
                self.logger.error("   ❌ Kein GeoTIFF gefunden im Download")
                return None
            
            self.logger.info(f"   📖 Lese GeoTIFF: {tif_path.name}")
            
            # *** WICHTIG: Lese alle Bänder auf einmal ***
            with rasterio.open(str(tif_path)) as ds:
                arr = ds.read(out_dtype='float32')  # Shape: (4, height, width)
                meta = ds.meta.copy()
            
            # Cleanup
            shutil.rmtree(tmpdir, ignore_errors=True)
            
            # *** WICHTIG: Korrekte Skalierung für Sentinel-2 SR ***
            # Sentinel-2 SR ist skaliert 0-10000
            rgb = (arr[:3] / 10000.0).clip(0, 1)  # Erste 3 Bänder
            nir = (arr[3] / 10000.0).clip(0, 1)   # 4. Band
            
            # Einzelbänder
            red = (arr[0] / 10000.0).clip(0, 1)
            green = (arr[1] / 10000.0).clip(0, 1)
            blue = (arr[2] / 10000.0).clip(0, 1)
            
            # RGB als uint8 für Visualisierung
            rgb_uint8 = (rgb * 255.0).astype('uint8').transpose(1, 2, 0)
            
            # Debug-Ausgabe
            self.logger.info(f"   ✅ Daten geladen: {arr.shape[2]}x{arr.shape[1]} Pixel")
            self.logger.info(f"   [DEBUG] Red:   Min={red.min():.3f}, Max={red.max():.3f}, Mean={red.mean():.3f}")
            self.logger.info(f"   [DEBUG] Green: Min={green.min():.3f}, Max={green.max():.3f}, Mean={green.mean():.3f}")
            self.logger.info(f"   [DEBUG] Blue:  Min={blue.min():.3f}, Max={blue.max():.3f}, Mean={blue.mean():.3f}")
            self.logger.info(f"   [DEBUG] NIR:   Min={nir.min():.3f}, Max={nir.max():.3f}, Mean={nir.mean():.3f}")
            
            # Validierung
            if red.max() < 0.01 or nir.max() < 0.01:
                self.logger.error("   ❌ Bänder scheinen leer zu sein (Max < 0.01)")
                return None
            
            # World-Parameter berechnen
            h, w = nir.shape
            nir_pixel_size_x = (max_lon - min_lon) / w
            nir_pixel_size_y = (max_lat - min_lat) / h
            
            nir_world_params = {
                'pixel_size_x': nir_pixel_size_x,
                'rotation_y': 0.0,
                'rotation_x': 0.0,
                'pixel_size_y': -nir_pixel_size_y,
                'upper_left_x': min_lon,
                'upper_left_y': max_lat
            }
            
            self.logger.info("✅ Google Earth Engine Daten erfolgreich geladen!")
            
            return {
                'red': red,
                'green': green,
                'blue': blue,
                'nir': nir,
                'rgb': rgb_uint8,
                'world_params': nir_world_params,
                'bbox': bbox
            }
            
        except Exception as e:
            self.logger.error(f"❌ Google Earth Engine Fehler: {e}")
            import traceback
            self.logger.debug(traceback.format_exc())
            return None
    
    def _download_nir_seasonal(self,
                               bbox: Tuple[float, float, float, float],
                               resolution: int = 10,
                               max_cloud_coverage: float = 20.0) -> Optional[Dict[str, np.ndarray]]:
        """
        SEASONAL Mode: Lädt beste Szene aus Crop Mark Saison (Juli-August).
        Optimiert für Crop Mark Detection.
        """
        if not self.initialized:
            self.logger.error("Google Earth Engine nicht initialisiert")
            return None
        
        try:
            import rasterio
            import zipfile
            import tempfile
            import shutil
            import requests
            from pathlib import Path
        except ImportError as e:
            self.logger.error(f"Benötigte Library fehlt: {e}")
            return None
        
        try:
            min_lon, min_lat, max_lon, max_lat = bbox
            
            self.logger.info(f"🌾 SEASONAL MODE: Lade beste Crop Mark Saison...")
            self.logger.info(f"   BBox: {min_lon:.6f},{min_lat:.6f} .. {max_lon:.6f},{max_lat:.6f}")
            self.logger.info(f"   Auflösung: {resolution}m")
            
            region = ee.Geometry.Rectangle([min_lon, min_lat, max_lon, max_lat], 
                                          proj='EPSG:4326', geodesic=False)
            
            def mask_s2_clouds(img):
                scl = img.select('SCL')
                mask = (scl.neq(3).And(scl.neq(8)).And(scl.neq(9))
                       .And(scl.neq(10)).And(scl.neq(11)))
                return img.updateMask(mask)
            
            # Crop Mark Saison: Juli-August der letzten 3 Jahre
            current_year = datetime.now().year
            years = [current_year, current_year - 1, current_year - 2]
            
            all_crop_scenes = []
            for year in years:
                collection = (ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
                             .filterBounds(region)
                             .filterDate(f'{year}-07-01', f'{year}-08-31')
                             .filter(ee.Filter.lte('CLOUDY_PIXEL_PERCENTAGE', max_cloud_coverage)))
                
                try:
                    collection = collection.map(mask_s2_clouds)
                except Exception:
                    pass
                
                count = collection.size().getInfo()
                if count > 0:
                    self.logger.info(f"   Jahr {year}: {count} Szenen gefunden")
                    all_crop_scenes.append(collection)
            
            if not all_crop_scenes:
                self.logger.warning("⚠️ Keine Crop Mark Szenen gefunden!")
                return None
            
            # Kombiniere alle Jahre
            combined = all_crop_scenes[0]
            for coll in all_crop_scenes[1:]:
                combined = combined.merge(coll)
            
            total = combined.size().getInfo()
            self.logger.info(f"   📊 Gesamt: {total} Szenen über {len(all_crop_scenes)} Jahre")
            
            # *** KRITISCHER FIX: Verwende Median-Composite statt .first()! ***
            # .first() nimmt nur EINE Szene → kann Lücken/NO-DATA Bereiche haben!
            # .median() kombiniert ALLE Szenen → füllt Lücken automatisch!
            self.logger.info(f"   📊 Erstelle Median-Composite aus {total} Szenen...")
            best_image = combined.median()  # ← GEÄNDERT von .first()!
            best_image = best_image.select(['B4', 'B3', 'B2', 'B8'])
            
            # Download wie in single-date...
            download_params = {
                'scale': int(resolution),
                'crs': 'EPSG:4326',
                'region': region,
                'format': 'GEO_TIFF',
                'filePerBand': False
            }
            
            url = best_image.getDownloadURL(download_params)
            
            self.logger.info(f"   📥 Downloade Daten (kann 30-90 Sekunden dauern)...")
            
            tmpdir = tempfile.mkdtemp()
            out_path = Path(tmpdir) / "s2_data.tif"
            
            response = requests.get(url, stream=True, timeout=600)
            response.raise_for_status()
            
            with open(out_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            
            self.logger.info(f"   ✅ Download abgeschlossen ({out_path.stat().st_size / 1024 / 1024:.2f} MB)")
            
            # Verarbeite wie in single-date
            tif_path = out_path
            
            if zipfile.is_zipfile(out_path):
                self.logger.info(f"   📦 Entpacke ZIP...")
                with zipfile.ZipFile(out_path, 'r') as zf:
                    zf.extractall(tmpdir)
                
                for p in Path(tmpdir).rglob('*.tif'):
                    tif_path = p
                    break
            
            with rasterio.open(tif_path) as src:
                red = src.read(1).astype('float32') / 10000.0
                green = src.read(2).astype('float32') / 10000.0
                blue = src.read(3).astype('float32') / 10000.0
                nir = src.read(4).astype('float32') / 10000.0
            
            shutil.rmtree(tmpdir)
            
            rgb_uint8 = np.stack([
                np.clip(red * 255, 0, 255).astype(np.uint8),
                np.clip(green * 255, 0, 255).astype(np.uint8),
                np.clip(blue * 255, 0, 255).astype(np.uint8)
            ], axis=-1)
            
            h, w = nir.shape
            nir_pixel_size_x = (max_lon - min_lon) / w
            nir_pixel_size_y = (max_lat - min_lat) / h
            
            nir_world_params = {
                'pixel_size_x': nir_pixel_size_x,
                'rotation_y': 0.0,
                'rotation_x': 0.0,
                'pixel_size_y': -nir_pixel_size_y,
                'upper_left_x': min_lon,
                'upper_left_y': max_lat
            }
            
            self.logger.info("✅ Seasonal Daten erfolgreich geladen!")
            
            return {
                'red': red,
                'green': green,
                'blue': blue,
                'nir': nir,
                'rgb': rgb_uint8,
                'world_params': nir_world_params,
                'bbox': bbox,
                'mode': 'seasonal'
            }
            
        except Exception as e:
            self.logger.error(f"❌ Seasonal Mode Fehler: {e}")
            import traceback
            self.logger.debug(traceback.format_exc())
            return None
    
    def _download_nir_multitemporal_ultimate(self,
                                              bbox: Tuple[float, float, float, float],
                                              resolution: int = 10,
                                              max_cloud_coverage: float = 20.0) -> Optional[Dict[str, np.ndarray]]:
        """
        ULTIMATE MODE: Multi-Temporal Analyse über 3 Jahre für MAXIMUM Treffsicherheit!
        
        Features:
        - Crop Mark Saison (Juli-Aug) über 3 Jahre
        - Soil Mark Saison (Apr-Mai) über 3 Jahre
        - Persistenz-Score (wie oft sichtbar)
        - Seasonal Contrast (Wachstums-Unterschied)
        - High Confidence Mask (nur sichere Anomalien)
        """
        if not self.initialized:
            self.logger.error("Google Earth Engine nicht initialisiert")
            return None
        
        try:
            import rasterio
            import zipfile
            import tempfile
            import shutil
            import requests
            from pathlib import Path
        except ImportError as e:
            self.logger.error(f"Benötigte Library fehlt: {e}")
            return None
        
        try:
            min_lon, min_lat, max_lon, max_lat = bbox
            
            self.logger.info(f"🚀 ULTIMATE MODE: Multi-Temporal Analyse über 3 Jahre...")
            self.logger.info(f"   BBox: {min_lon:.6f},{min_lat:.6f} .. {max_lon:.6f},{max_lat:.6f}")
            self.logger.info(f"   Auflösung: {resolution}m")
            self.logger.info(f"   Dies kann 60-120 Sekunden dauern...")
            
            region = ee.Geometry.Rectangle([min_lon, min_lat, max_lon, max_lat], 
                                          proj='EPSG:4326', geodesic=False)
            
            def mask_s2_clouds(img):
                scl = img.select('SCL')
                mask = (scl.neq(3).And(scl.neq(8)).And(scl.neq(9))
                       .And(scl.neq(10)).And(scl.neq(11)))
                return img.updateMask(mask)
            
            current_year = datetime.now().year
            years = [current_year, current_year - 1, current_year - 2]
            
            # ═══════════════════════════════════════════════════════════
            # SCHRITT 1: Crop Mark Saison (Juli-August) über 3 Jahre
            # ═══════════════════════════════════════════════════════════
            
            self.logger.info(f"   📅 Lade Crop Mark Saisons (Juli-Aug)...")
            crop_images = []
            crop_ndvis = []
            
            for year in years:
                collection = (ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
                             .filterBounds(region)
                             .filterDate(f'{year}-07-01', f'{year}-08-31')
                             .filter(ee.Filter.lte('CLOUDY_PIXEL_PERCENTAGE', 15)))  # Strenger!
                
                try:
                    collection = collection.map(mask_s2_clouds)
                except Exception:
                    pass
                
                count = collection.size().getInfo()
                if count > 0:
                    # *** FIX: Verwende Median pro Jahr statt .first() ***
                    # Verhindert NO-DATA Bereiche innerhalb eines Jahres!
                    best = collection.median()  # ← GEÄNDERT von .first()
                    crop_images.append(best)
                    
                    # NDVI berechnen
                    ndvi = best.normalizedDifference(['B8', 'B4']).rename('NDVI')
                    crop_ndvis.append(ndvi)
                    
                    self.logger.info(f"      {year}: {count} Szenen → Median-Composite erstellt")
            
            if len(crop_images) < 2:
                self.logger.warning("   ⚠️ Zu wenig Crop Mark Daten, fallback zu seasonal...")
                return self._download_nir_seasonal(bbox, resolution, max_cloud_coverage)
            
            # ═══════════════════════════════════════════════════════════
            # SCHRITT 2: Soil Mark Saison (April-Mai) über 3 Jahre
            # ═══════════════════════════════════════════════════════════
            
            self.logger.info(f"   📅 Lade Soil Mark Saisons (Apr-Mai)...")
            soil_images = []
            soil_ndvis = []
            
            for year in years:
                collection = (ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
                             .filterBounds(region)
                             .filterDate(f'{year}-04-01', f'{year}-05-31')
                             .filter(ee.Filter.lte('CLOUDY_PIXEL_PERCENTAGE', 15)))
                
                try:
                    collection = collection.map(mask_s2_clouds)
                except Exception:
                    pass
                
                count = collection.size().getInfo()
                if count > 0:
                    # *** FIX: Verwende Median pro Jahr statt .first() ***
                    best = collection.median()  # ← GEÄNDERT von .first()
                    soil_images.append(best)
                    
                    ndvi = best.normalizedDifference(['B8', 'B4']).rename('NDVI')
                    soil_ndvis.append(ndvi)
                    
                    self.logger.info(f"      {year}: {count} Szenen → Median-Composite erstellt")
            
            # ═══════════════════════════════════════════════════════════
            # SCHRITT 3: PERSISTENZ-ANALYSE
            # ═══════════════════════════════════════════════════════════
            
            self.logger.info(f"   🔍 Berechne Persistenz-Score...")
            
            # Zähle wie oft NDVI < 0.5 (Anomalie-Threshold)
            anomaly_threshold = 0.5
            anomaly_masks = [ndvi.lt(anomaly_threshold) for ndvi in crop_ndvis]
            
            # Summiere: 0 = nie Anomalie, 3 = immer Anomalie
            if len(anomaly_masks) > 0:
                persistence = anomaly_masks[0]
                for mask in anomaly_masks[1:]:
                    persistence = persistence.add(mask)
            else:
                persistence = ee.Image.constant(0)
            
            self.logger.info(f"      ✅ Persistenz über {len(crop_ndvis)} Jahre berechnet")
            
            # ═══════════════════════════════════════════════════════════
            # SCHRITT 4: SEASONAL CONTRAST
            # ═══════════════════════════════════════════════════════════
            
            self.logger.info(f"   📊 Berechne Seasonal Contrast...")
            
            # Median über alle Crop Szenen
            crop_composite = ee.ImageCollection(crop_ndvis).median().rename('CROP_NDVI')
            
            # Median über alle Soil Szenen (falls vorhanden)
            if len(soil_ndvis) > 0:
                soil_composite = ee.ImageCollection(soil_ndvis).median().rename('SOIL_NDVI')
                
                # Kontrast = Sommer - Frühjahr
                seasonal_contrast = crop_composite.subtract(soil_composite).rename('CONTRAST')
                
                self.logger.info(f"      ✅ Contrast berechnet (Crop - Soil)")
            else:
                # Fallback: Kein Soil verfügbar
                seasonal_contrast = ee.Image.constant(0).rename('CONTRAST')
                self.logger.info(f"      ⚠️ Kein Soil verfügbar, Contrast = 0")
            
            # ═══════════════════════════════════════════════════════════
            # SCHRITT 5: HIGH CONFIDENCE MASK
            # ═══════════════════════════════════════════════════════════
            
            self.logger.info(f"   🎯 Berechne High Confidence Mask...")
            
            # Hochsichere Anomalien erfüllen ALLE Kriterien:
            # 1. Persistenz ≥ 2 (mindestens 2 Jahre sichtbar)
            # 2. Seasonal Contrast < 0.2 (wenig Wachstum)
            # 3. Crop NDVI < 0.4 (sehr niedrig)
            
            high_confidence = (
                persistence.gte(2)
                .And(seasonal_contrast.lt(0.2))
                .And(crop_composite.lt(0.4))
            ).rename('HIGH_CONF')
            
            self.logger.info(f"      ✅ High Confidence: Persistenz≥2 AND Contrast<0.2 AND NDVI<0.4")
            
            # ═══════════════════════════════════════════════════════════
            # SCHRITT 6: FINALE COMPOSITE ERSTELLEN
            # ═══════════════════════════════════════════════════════════
            
            self.logger.info(f"   🎨 Erstelle finales Composite...")
            
            # *** FIX: Verwende Median über ALLE Jahre, nicht nur erstes Bild! ***
            # crop_images[0] würde nur EIN Jahr nehmen → potenzielle Lücken!
            # Median kombiniert alle Jahre → maximale Coverage!
            best_crop = ee.ImageCollection(crop_images).median()
            self.logger.info(f"      Median über {len(crop_images)} Jahre erstellt")
            
            # Füge alle Metriken hinzu
            final_image = best_crop.select(['B4', 'B3', 'B2', 'B8']).addBands([
                persistence.rename('PERSISTENCE'),
                seasonal_contrast,
                high_confidence
            ])
            
            # ═══════════════════════════════════════════════════════════
            # SCHRITT 7: DOWNLOAD
            # ═══════════════════════════════════════════════════════════
            
            download_params = {
                'scale': int(resolution),
                'crs': 'EPSG:4326',
                'region': region,
                'format': 'GEO_TIFF',
                'filePerBand': False
            }
            
            url = final_image.getDownloadURL(download_params)
            
            self.logger.info(f"   📥 Downloade Multi-Temporal Daten...")
            
            tmpdir = tempfile.mkdtemp()
            out_path = Path(tmpdir) / "s2_multitemporal.tif"
            
            response = requests.get(url, stream=True, timeout=600)
            response.raise_for_status()
            
            with open(out_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            
            self.logger.info(f"   ✅ Download abgeschlossen ({out_path.stat().st_size / 1024 / 1024:.2f} MB)")
            
            # Entpacke falls ZIP
            tif_path = out_path
            if zipfile.is_zipfile(out_path):
                self.logger.info(f"   📦 Entpacke ZIP...")
                with zipfile.ZipFile(out_path, 'r') as zf:
                    zf.extractall(tmpdir)
                
                for p in Path(tmpdir).rglob('*.tif'):
                    tif_path = p
                    break
            
            # Lese alle Bänder
            with rasterio.open(tif_path) as src:
                red = src.read(1).astype('float32') / 10000.0
                green = src.read(2).astype('float32') / 10000.0
                blue = src.read(3).astype('float32') / 10000.0
                nir = src.read(4).astype('float32') / 10000.0
                
                # Multi-Temporal Metriken (Bänder 5-7)
                persistence_array = src.read(5).astype('float32')
                contrast_array = src.read(6).astype('float32')
                high_conf_array = src.read(7).astype('float32')
            
            shutil.rmtree(tmpdir)
            
            rgb_uint8 = np.stack([
                np.clip(red * 255, 0, 255).astype(np.uint8),
                np.clip(green * 255, 0, 255).astype(np.uint8),
                np.clip(blue * 255, 0, 255).astype(np.uint8)
            ], axis=-1)
            
            h, w = nir.shape
            nir_pixel_size_x = (max_lon - min_lon) / w
            nir_pixel_size_y = (max_lat - min_lat) / h
            
            nir_world_params = {
                'pixel_size_x': nir_pixel_size_x,
                'rotation_y': 0.0,
                'rotation_x': 0.0,
                'pixel_size_y': -nir_pixel_size_y,
                'upper_left_x': min_lon,
                'upper_left_y': max_lat
            }
            
            # Statistiken
            high_conf_percent = (high_conf_array > 0.5).sum() / high_conf_array.size * 100
            persist_mean = persistence_array.mean()
            
            self.logger.info("✅ ULTIMATE Multi-Temporal Daten erfolgreich geladen!")
            self.logger.info(f"   📊 High Confidence Pixel: {high_conf_percent:.2f}%")
            self.logger.info(f"   📊 Mittlere Persistenz: {persist_mean:.2f}")
            
            return {
                'red': red,
                'green': green,
                'blue': blue,
                'nir': nir,
                'rgb': rgb_uint8,
                'world_params': nir_world_params,
                'bbox': bbox,
                
                # *** Multi-Temporal Metriken ***
                'persistence_score': persistence_array,     # 0-3 (Anzahl Jahre sichtbar)
                'seasonal_contrast': contrast_array,        # Sommer - Frühjahr NDVI
                'high_confidence_mask': high_conf_array,    # 0 oder 1
                
                'mode': 'ultimate',
                'years_analyzed': len(crop_images)
            }
            
        except Exception as e:
            self.logger.error(f"❌ Ultimate Multi-Temporal Fehler: {e}")
            import traceback
            self.logger.debug(traceback.format_exc())
            self.logger.warning("   ⚠️ Fallback zu seasonal mode...")
            return self._download_nir_seasonal(bbox, resolution, max_cloud_coverage)
    
    def calculate_bbox_from_pgw(self, world_params: Dict, image_shape: Tuple[int, int]) -> Tuple[float, float, float, float]:
        """
        Berechnet BBox aus World-File Parametern.
        
        Returns:
            (min_lon, min_lat, max_lon, max_lat)
        """
        height, width = image_shape[:2]
        
        # Obere linke Ecke
        upper_left_x = world_params['upper_left_x']
        upper_left_y = world_params['upper_left_y']
        
        # Untere rechte Ecke
        lower_right_x = upper_left_x + width * world_params['pixel_size_x']
        lower_right_y = upper_left_y + height * world_params['pixel_size_y']
        
        # BBox (min_lon, min_lat, max_lon, max_lat)
        min_lon = min(upper_left_x, lower_right_x)
        max_lon = max(upper_left_x, lower_right_x)
        min_lat = min(upper_left_y, lower_right_y)
        max_lat = max(upper_left_y, lower_right_y)
        
        return (min_lon, min_lat, max_lon, max_lat)


class UltimateTreasureFinder:
    """
    ULTIMATE Treasure Finder mit Google Earth Engine NIR-Integration.
    """
    
    def __init__(self, log_level=logging.INFO, gee_config: Optional[Dict] = None):
        self.lidar_bounds = None
        self.historical_bounds = None
        self.aerial_bounds = None
        self.potential_sites = []
        
        # *** BUGFIX #3: Initialisiere multi_temporal_mode Attribut ***
        self.multi_temporal_mode = 'ultimate'
        
        # Logging Setup
        self.logger = logging.getLogger('TreasureFinder')
        self.logger.setLevel(log_level)
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter('[%(levelname)s] %(message)s')
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
        
        # Google Earth Engine Connector (optional)
        self.gee_connector = None
        if gee_config:
            if GEE_AVAILABLE:
                self.gee_connector = GoogleEarthEngineConnector(
                    project_id=gee_config.get('project_id'),
                    logger=self.logger
                )
            else:
                self.logger.warning("⚠️ earthengine-api library nicht installiert")
                self.logger.warning("   Installation: pip install earthengine-api")
    
    def _ndvi_to_rgb(self, ndvi: np.ndarray) -> np.ndarray:
        """
        Map NDVI float array to RGB uint8 for visual debug.
        *** ÜBERNOMMEN vom funktionierenden Code ***
        Color scheme: Blue -> Green -> Yellow -> Red
        Range: -0.2 (blue) to 0.9 (red)
        """
        x = ndvi.astype("float32")
        x = np.clip(x, -0.2, 0.9)
        x = (x + 0.2) / 1.1  # Normalisiere -0.2...0.9 auf 0...1
        x = np.clip(x, 0.0, 1.0)
        rgb = np.zeros((x.shape[0], x.shape[1], 3), dtype="float32")
        
        # Phase 1: 0.0 - 0.5 → Blue to Green
        m1 = x <= 0.5
        t1 = np.zeros_like(x)
        t1[m1] = x[m1] / 0.5
        rgb[m1, 1] = 255.0 * t1[m1]      # Green steigt
        rgb[m1, 2] = 255.0 * (1.0 - t1[m1])  # Blue fällt
        
        # Phase 2: 0.5 - 0.75 → Green to Yellow
        m2 = (x > 0.5) & (x <= 0.75)
        t2 = (x[m2] - 0.5) / 0.25
        rgb[m2, 0] = 255.0 * t2  # Red steigt
        rgb[m2, 1] = 255.0       # Green bleibt
        
        # Phase 3: 0.75 - 1.0 → Yellow to Red
        m3 = x > 0.75
        t3 = (x[m3] - 0.75) / 0.25
        rgb[m3, 0] = 255.0            # Red bleibt
        rgb[m3, 1] = 255.0 * (1.0 - t3)  # Green fällt
        
        return np.clip(rgb, 0, 255).astype("uint8")
    
    def parse_world_file(self, world_file_path: str) -> Optional[Dict]:
        """Parst World-Datei mit Fehlerbehandlung."""
        try:
            with open(world_file_path, 'r') as f:
                lines = [float(line.strip()) for line in f.readlines()]
            
            if len(lines) < 6:
                self.logger.error(f"World-Datei {world_file_path} hat nur {len(lines)} Zeilen (6 erwartet)")
                return None
            
            return {
                'pixel_size_x': lines[0],
                'rotation_y': lines[1],
                'rotation_x': lines[2],
                'pixel_size_y': lines[3],
                'upper_left_x': lines[4],
                'upper_left_y': lines[5]
            }
        except FileNotFoundError:
            self.logger.error(f"World-Datei nicht gefunden: {world_file_path}")
            return None
        except ValueError as e:
            self.logger.error(f"Fehler beim Parsen von {world_file_path}: {e}")
            return None
        except Exception as e:
            self.logger.error(f"Unerwarteter Fehler beim Lesen von {world_file_path}: {e}")
            return None
    
    def pixel_to_coords(self, px: int, py: int, world_params: Dict) -> Tuple[float, float]:
        """Konvertiert Pixel zu Koordinaten."""
        x = world_params['upper_left_x'] + px * world_params['pixel_size_x']
        y = world_params['upper_left_y'] + py * world_params['pixel_size_y']
        return x, y
    
    def load_image(self, image_path: str, as_rgb: bool = True) -> Optional[np.ndarray]:
        """
        Lädt Bild mit Fehlerbehandlung und korrekter Farbkonvertierung.
        """
        try:
            img = cv2.imread(image_path)
            
            if img is None:
                self.logger.error(f"Bild konnte nicht geladen werden: {image_path}")
                return None
            
            if as_rgb:
                img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            
            self.logger.info(f"Bild geladen: {image_path} ({img.shape[1]}x{img.shape[0]})")
            return img
            
        except Exception as e:
            self.logger.error(f"Fehler beim Laden von {image_path}: {e}")
            return None
    
    def validate_image_sizes(self, images: Dict[str, np.ndarray]) -> bool:
        """Validiert ob alle Bilder kompatible Größen haben."""
        shapes = {name: img.shape[:2] for name, img in images.items()}
        
        self.logger.info("Bildgrößen:")
        for name, shape in shapes.items():
            self.logger.info(f"  {name}: {shape[1]}x{shape[0]}")
        
        unique_shapes = set(shapes.values())
        if len(unique_shapes) > 1:
            self.logger.warning("⚠️ Bilder haben unterschiedliche Größen - es wird automatisch skaliert")
            self.logger.warning("⚠️ Dies kann zu Ungenauigkeiten führen!")
            return False
        
        return True
    
    def calculate_true_ndvi(self, nir: np.ndarray, red: np.ndarray) -> np.ndarray:
        """
        Berechnet ECHTEN NDVI mit NIR-Daten.
        
        NDVI = (NIR - RED) / (NIR + RED)
        Werte: -1 bis +1 (NICHT normalisiert!)
        """
        try:
            ndvi = (nir - red) / (nir + red + 1e-6)
            
            self.logger.info(f"  NDVI Bereich: {ndvi.min():.3f} bis {ndvi.max():.3f}")
            
            return ndvi.astype('float32')
        except Exception as e:
            self.logger.error(f"Fehler bei NDVI-Berechnung: {e}")
            return np.zeros_like(nir)
    
    def calculate_evi(self, nir: np.ndarray, red: np.ndarray, blue: np.ndarray) -> np.ndarray:
        """
        Enhanced Vegetation Index (EVI).
        Bessere Performance bei hoher Biomasse.
        
        EVI = 2.5 * (NIR - RED) / (NIR + 6*RED - 7.5*BLUE + 1)
        
        *** WICHTIG: Original Range beibehalten (nicht normalisieren!) ***
        Typische Werte: -1 bis +1
        """
        try:
            evi = 2.5 * (nir - red) / (nir + 6.0 * red - 7.5 * blue + 1.0)
            
            self.logger.info(f"  EVI Bereich: {evi.min():.3f} bis {evi.max():.3f}")
            
            return evi.astype('float32')
        except Exception as e:
            self.logger.error(f"Fehler bei EVI-Berechnung: {e}")
            return np.zeros_like(nir)
    
    def calculate_savi(self, nir: np.ndarray, red: np.ndarray, L: float = 0.5) -> np.ndarray:
        """
        Soil-Adjusted Vegetation Index (SAVI).
        Minimiert Bodeneinfluss - perfekt für archäologische Sites!
        
        SAVI = ((NIR - RED) / (NIR + RED + L)) * (1 + L)
        L = 0.5 für mittlere Vegetation
        
        *** WICHTIG: Original Range beibehalten (nicht normalisieren!) ***
        Typische Werte: 0.0 bis ~0.5
        """
        try:
            savi = ((nir - red) / (nir + red + L)) * (1.0 + L)
            
            self.logger.info(f"  SAVI Bereich: {savi.min():.3f} bis {savi.max():.3f}")
            
            return savi.astype('float32')
        except Exception as e:
            self.logger.error(f"Fehler bei SAVI-Berechnung: {e}")
            return np.zeros_like(nir)
    
    def calculate_ndwi(self, nir: np.ndarray, green: np.ndarray) -> np.ndarray:
        """
        Normalized Difference Water Index (NDWI).
        Erkennt Wasserstrukturen (alte Kanäle, Gräben).
        
        NDWI = (GREEN - NIR) / (GREEN + NIR)
        
        *** WISSENSCHAFTLICHE ANMERKUNG: ***
        Es gibt zwei NDWI-Formeln:
        1. McFeeters (1996): (Green - NIR) / (Green + NIR) - für offenes Wasser [VERWENDET]
        2. Gao (1996): (NIR - SWIR) / (NIR + SWIR) - für Vegetationswasser
        
        Diese Implementation nutzt McFeeters NDWI, da Sentinel-2 SWIR nicht immer
        verfügbar ist und für archäologische Wassergräben/Kanäle ausreichend.
        
        *** WICHTIG: Original Range beibehalten (nicht normalisieren!) ***
        Typische Werte: -1 bis +1
        Schwellwerte: > 0.3 = Wasser, > 0.7 = permanentes Wasser
        """
        try:
            ndwi = (green - nir) / (green + nir + 1e-8)
            
            self.logger.info(f"  NDWI Bereich: {ndwi.min():.3f} bis {ndwi.max():.3f}")
            
            return ndwi.astype('float32')
        except Exception as e:
            self.logger.error(f"Fehler bei NDWI-Berechnung: {e}")
            return np.zeros_like(nir)
    
    def normalize_for_fusion(self, data: np.ndarray, expected_range: Tuple[float, float]) -> np.ndarray:
        """
        Normalisiert Daten auf [0, 1] für weighted sums.
        
        Args:
            data: Input array
            expected_range: (min, max) erwartete Range für den Index
        
        Returns:
            Normalisierte Daten [0, 1]
        """
        min_val, max_val = expected_range
        normalized = (data - min_val) / (max_val - min_val)
        return np.clip(normalized, 0, 1).astype('float32')
    
    def detect_nir_vegetation_stress_advanced(self, nir: np.ndarray, red: np.ndarray, 
                                              green: np.ndarray, blue: np.ndarray) -> Dict:
        """
        ERWEITERTE Vegetationsanalyse mit echten NIR-Daten.
        *** OPTIMIERT: Crop Marks nutzen jetzt SAVI + NDVI Varianz ***
        """
        self.logger.info("  [NIR] Berechne Vegetation Indices...")
        
        # Echte Indices (Original Ranges!)
        ndvi = self.calculate_true_ndvi(nir, red)       # -1 bis +1
        evi = self.calculate_evi(nir, red, blue)        # -1 bis +1
        savi = self.calculate_savi(nir, red)            # 0 bis ~0.5
        ndwi = self.calculate_ndwi(nir, green)          # -1 bis +1
        
        # Vegetationsstress = Niedriger NDVI
        # *** FIX: NDVI ist -1 bis +1, normalisiere zuerst! ***
        ndvi_norm = self.normalize_for_fusion(ndvi, (-1.0, 1.0))  # Jetzt 0-1
        veg_stress = 1.0 - ndvi_norm
        
        # *** OPTIMIERT: Crop Mark Detektion mit NDVI + SAVI Varianz ***
        # NDVI-Varianz
        ndvi_blur = cv2.GaussianBlur(ndvi, (15, 15), 0)
        ndvi_variance = np.abs(ndvi - ndvi_blur)
        ndvi_var_norm = (ndvi_variance - ndvi_variance.min()) / (ndvi_variance.max() - ndvi_variance.min() + 1e-8)
        
        # SAVI-Varianz (neu!)
        savi_blur = cv2.GaussianBlur(savi, (15, 15), 0)
        savi_variance = np.abs(savi - savi_blur)
        savi_var_norm = (savi_variance - savi_variance.min()) / (savi_variance.max() - savi_variance.min() + 1e-8)
        
        # Kombiniere: SAVI wichtiger (zuverlässiger für Archäologie)!
        crop_mark_potential = 0.35 * ndvi_var_norm + 0.65 * savi_var_norm
        
        # *** FÜR WEIGHTED SUM: Normalisiere alle auf 0-1 ***
        ndvi_for_fusion = self.normalize_for_fusion(ndvi, (-1.0, 1.0))
        evi_for_fusion = self.normalize_for_fusion(evi, (-1.0, 1.0))
        savi_for_fusion = self.normalize_for_fusion(savi, (0.0, 0.5))
        ndwi_for_fusion = self.normalize_for_fusion(ndwi, (-1.0, 1.0))
        
        return {
            # Original Ranges (für Schwellwerte!)
            'ndvi': ndvi,
            'evi': evi,
            'savi': savi,
            'ndwi': ndwi,
            # Normalisiert für weighted sums
            'ndvi_fusion': ndvi_for_fusion,
            'evi_fusion': evi_for_fusion,
            'savi_fusion': savi_for_fusion,
            'ndwi_fusion': ndwi_for_fusion,
            # Andere
            'veg_stress': veg_stress,
            'crop_mark_potential': crop_mark_potential
        }
    
    def detect_crop_marks(self, aerial_rgb: np.ndarray) -> Dict:
        """
        Erkennt Crop Marks (RGB-basiert, Fallback wenn kein NIR).
        
        *** FIX v4.2.0: Varianz-Analyse + A-Channel hinzugefügt! ***
        """
        try:
            lab = cv2.cvtColor(aerial_rgb, cv2.COLOR_RGB2LAB)
            l_channel, a_channel, b_channel = cv2.split(lab)
            
            # GREEN CHANNEL mit Varianz-Analyse
            green_intensity = aerial_rgb[:, :, 1].astype(float)
            green_norm = (green_intensity - green_intensity.min()) / (green_intensity.max() - green_intensity.min() + 1e-8)
            
            # *** FIX v4.2.0: Varianz-basierte Crop Mark Detektion ***
            green_blur = cv2.GaussianBlur(green_norm, (15, 15), 0)
            green_variance = np.abs(green_norm - green_blur)
            green_var_norm = (green_variance - green_variance.min()) / (green_variance.max() - green_variance.min() + 1e-8)
            
            # ExG (Excess Green Index)
            r = aerial_rgb[:, :, 0].astype(float) / 255.0
            g = aerial_rgb[:, :, 1].astype(float) / 255.0
            b = aerial_rgb[:, :, 2].astype(float) / 255.0
            
            exg = 2 * g - r - b
            exg_norm = (exg - exg.min()) / (exg.max() - exg.min() + 1e-8)
            
            # *** FIX v4.2.0: A-Channel (Grün-Rot Achse) nutzen! ***
            # Negative A = Grün-dominant (Vegetation)
            # Positive A = Rot-dominant (Boden, Stress)
            a_float = a_channel.astype(float)
            a_norm = (a_float - a_float.min()) / (a_float.max() - a_float.min() + 1e-8)
            
            # Varianz im A-Channel = Vegetation-Anomalien
            a_blur = cv2.GaussianBlur(a_float, (15, 15), 0)
            a_variance = np.abs(a_float - a_blur)
            a_var_norm = (a_variance - a_variance.min()) / (a_variance.max() - a_variance.min() + 1e-8)
            
            # L-Channel Varianz (Helligkeit)
            l_float = l_channel.astype(float)
            l_blur = cv2.GaussianBlur(l_float, (15, 15), 0)
            l_variation = np.abs(l_float - l_blur)
            l_var_norm = (l_variation - l_variation.min()) / (l_variation.max() - l_variation.min() + 1e-8)
            
            # Textur
            gray = cv2.cvtColor(aerial_rgb, cv2.COLOR_RGB2GRAY)
            texture = cv2.Laplacian(gray, cv2.CV_64F)
            texture_abs = np.abs(texture)
            texture_norm = (texture_abs - texture_abs.min()) / (texture_abs.max() - texture_abs.min() + 1e-8)
            
            # *** OPTIMIERTE GEWICHTUNG v4.2.0 ***
            crop_marks = (
                0.25 * green_var_norm +    # Grün-Varianz (neu!)
                0.20 * exg_norm +           # ExG Index
                0.20 * a_var_norm +         # A-Channel Varianz (neu!)
                0.20 * l_var_norm +         # Helligkeit-Varianz
                0.15 * texture_norm         # Textur
            )
            
            return {
                'crop_marks': crop_marks,
                'green_intensity': green_norm,
                'green_variance': green_var_norm,     # *** NEU ***
                'a_channel_variance': a_var_norm,      # *** NEU ***
                'exg_index': exg_norm,
                'brightness_var': l_var_norm,
                'texture': texture_norm
            }
        except Exception as e:
            self.logger.error(f"Fehler bei Crop Mark Detektion: {e}")
            import traceback
            self.logger.debug(traceback.format_exc())
            h, w = aerial_rgb.shape[:2]
            empty = np.zeros((h, w), dtype=float)
            return {
                'crop_marks': empty, 'green_intensity': empty,
                'green_variance': empty, 'a_channel_variance': empty,
                'exg_index': empty, 'brightness_var': empty, 'texture': empty
            }
    
    def detect_soil_marks(self, aerial_rgb: np.ndarray) -> np.ndarray:
        """Erkennt Soil Marks."""
        try:
            # Stelle sicher dass Input uint8 ist (0-255)
            if aerial_rgb.dtype != np.uint8:
                aerial_rgb = (np.clip(aerial_rgb, 0, 1) * 255).astype(np.uint8)
            
            # Prüfe Shape
            if len(aerial_rgb.shape) != 3 or aerial_rgb.shape[2] != 3:
                raise ValueError(f"Ungültiges RGB-Format: {aerial_rgb.shape}")
            
            hsv = cv2.cvtColor(aerial_rgb, cv2.COLOR_RGB2HSV)
            h, s, v = cv2.split(hsv)
            
            s_float = s.astype(float)
            s_blur = cv2.GaussianBlur(s_float, (21, 21), 0)
            s_anomaly = np.abs(s_float - s_blur)
            s_norm = (s_anomaly - s_anomaly.min()) / (s_anomaly.max() - s_anomaly.min() + 1e-8)
            
            h_float = h.astype(float)
            h_blur = cv2.GaussianBlur(h_float, (21, 21), 0)
            h_anomaly = np.abs(h_float - h_blur)
            h_norm = (h_anomaly - h_anomaly.min()) / (h_anomaly.max() - h_anomaly.min() + 1e-8)
            
            soil_marks = 0.6 * s_norm + 0.4 * h_norm
            
            return soil_marks
        except Exception as e:
            self.logger.warning(f"⚠️ Soil Mark Detektion übersprungen: {e}")
            return np.zeros(aerial_rgb.shape[:2], dtype=float)
    
    def create_urban_mask(self, aerial_rgb: np.ndarray, nir: np.ndarray = None) -> np.ndarray:
        """
        Erstellt eine Maske für urbane Bereiche (Straßen, Gebäude).
        
        Returns:
            Binary mask: 1 = urban (ausschließen), 0 = ländlich (behalten)
        """
        self.logger.info("  [URBAN] Erstelle Urban Mask...")
        
        h, w = aerial_rgb.shape[:2]
        urban_score = np.zeros((h, w), dtype=float)
        
        try:
            # Stelle sicher dass RGB uint8 ist
            if aerial_rgb.dtype != np.uint8:
                rgb_uint8 = (np.clip(aerial_rgb, 0, 1) * 255).astype(np.uint8)
            else:
                rgb_uint8 = aerial_rgb
            
            # 1. NDVI-basiert: Sehr niedriger NDVI = Asphalt/Beton
            if nir is not None:
                red = aerial_rgb[:, :, 0] if aerial_rgb.dtype == float else aerial_rgb[:, :, 0] / 255.0
                ndvi = (nir - red) / (nir + red + 1e-8)
                # Asphalt/Beton hat NDVI < 0.2
                asphalt_mask = ndvi < 0.2
                urban_score += asphalt_mask.astype(float) * 0.5
            
            # 2. Grau-Detektion: Straßen/Gebäude sind oft grau
            hsv = cv2.cvtColor(rgb_uint8, cv2.COLOR_RGB2HSV)
            saturation = hsv[:, :, 1].astype(float) / 255.0
            
            # Graue Bereiche (niedrige Saturation)
            gray_mask = saturation < 0.15
            urban_score += gray_mask.astype(float) * 0.3
            
            # 3. Kantendetektion: Gebäude haben viele Kanten
            gray = cv2.cvtColor(rgb_uint8, cv2.COLOR_RGB2GRAY)
            edges = cv2.Canny(gray, 50, 150)
            edges_dilated = cv2.dilate(edges, np.ones((5,5), np.uint8), iterations=2)
            
            # Hohe Kantendichte = wahrscheinlich Gebäude
            kernel = np.ones((20, 20), np.float32) / 400
            edge_density = cv2.filter2D(edges_dilated.astype(float), -1, kernel)
            edge_mask = edge_density > 0.1
            
            urban_score += edge_mask.astype(float) * 0.2
            
            # Normalisierung & Schwellwert
            urban_score = np.clip(urban_score, 0, 1)
            
            # Binary: > 0.5 = urban
            urban_binary = (urban_score > 0.5).astype(np.uint8)
            
            # Morphologische Operationen (schließe Lücken)
            kernel = np.ones((10, 10), np.uint8)
            urban_binary = cv2.morphologyEx(urban_binary, cv2.MORPH_CLOSE, kernel)
            
            urban_percentage = (urban_binary.sum() / urban_binary.size) * 100
            self.logger.info(f"  [URBAN] {urban_percentage:.1f}% als urban klassifiziert")
            
            return urban_binary.astype(float)
            
        except Exception as e:
            self.logger.warning(f"⚠️ Urban Mask fehlgeschlagen: {e}")
            return np.zeros((h, w), dtype=float)
    
    def multiscale_analysis(self, image: np.ndarray, scales: List[int] = [3, 7, 15, 31]) -> np.ndarray:
        """Multiskalenanalyse."""
        try:
            if len(image.shape) == 3:
                gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
            else:
                gray = image.copy()
            
            multiscale_features = []
            
            for scale in scales:
                blurred = cv2.GaussianBlur(gray, (scale, scale), 0)
                log = cv2.Laplacian(blurred, cv2.CV_64F)
                log_abs = np.abs(log)
                log_norm = (log_abs - log_abs.min()) / (log_abs.max() - log_abs.min() + 1e-8)
                multiscale_features.append(log_norm)
            
            combined = np.mean(multiscale_features, axis=0)
            return combined
        except Exception as e:
            self.logger.error(f"Fehler bei Multiskalen-Analyse: {e}")
            return np.zeros(image.shape[:2], dtype=float)
    
    def detect_geometric_patterns(self, binary_image: np.ndarray) -> Dict:
        """Erkennt geometrische Muster."""
        try:
            contours, _ = cv2.findContours(
                binary_image.astype(np.uint8), 
                cv2.RETR_EXTERNAL, 
                cv2.CHAIN_APPROX_SIMPLE
            )
            
            rectangles = np.zeros_like(binary_image, dtype=float)
            circles = np.zeros_like(binary_image, dtype=float)
            
            for contour in contours:
                area = cv2.contourArea(contour)
                if area < 20:
                    continue
                
                peri = cv2.arcLength(contour, True)
                approx = cv2.approxPolyDP(contour, 0.04 * peri, True)
                
                if len(approx) == 4:
                    cv2.drawContours(rectangles, [contour], -1, 1.0, -1)
                
                (x, y), radius = cv2.minEnclosingCircle(contour)
                circle_area = np.pi * radius * radius
                circularity = area / (circle_area + 1e-8)
                
                if 0.7 < circularity < 1.3:
                    cv2.circle(circles, (int(x), int(y)), int(radius), 1.0, -1)
            
            return {
                'rectangles': rectangles,
                'circles': circles
            }
        except Exception as e:
            self.logger.error(f"Fehler bei geometrischer Mustererkennung: {e}")
            h, w = binary_image.shape
            return {
                'rectangles': np.zeros((h, w), dtype=float),
                'circles': np.zeros((h, w), dtype=float)
            }
    
    def advanced_aerial_analysis(self, aerial_rgb: Optional[np.ndarray] = None,
                                 nir_data: Optional[Dict] = None) -> Tuple[np.ndarray, Dict]:
        """
        Erweiterte Luftbildanalyse - MIT oder OHNE NIR.
        """
        if nir_data is not None:
            # *** PREMIUM MODE: Echte NIR-Daten verfügbar! ***
            self.logger.info("  🌈 [NIR MODE] Verwende echte Satellitendaten!")
            
            # *** BUGFIX #1b: Zusätzliche NULL-Checks für nir_data Inhalte ***
            if not all(k in nir_data for k in ['nir', 'red', 'green', 'blue', 'rgb']):
                self.logger.error("❌ NIR-Daten unvollständig! Fehlende Bänder.")
                self.logger.error(f"   Vorhanden: {list(nir_data.keys())}")
                return None, {}
            
            if any(nir_data[k] is None for k in ['nir', 'red', 'green', 'blue', 'rgb']):
                self.logger.error("❌ NIR-Daten enthalten None-Werte!")
                return None, {}
            
            # NIR-basierte Analyse
            nir_indices = self.detect_nir_vegetation_stress_advanced(
                nir_data['nir'], 
                nir_data['red'],
                nir_data['green'],
                nir_data['blue']
            )
            
            # RGB für Soil Marks
            rgb_for_soil = nir_data['rgb']
            self.logger.info("  [SOIL] Soil Marks...")
            soil_marks = self.detect_soil_marks(rgb_for_soil)
            
            self.logger.info("  [MULTI] Multiskalen...")
            multiscale = self.multiscale_analysis(rgb_for_soil)
            
            # PREMIUM FUSION mit NIR-Indices
            # *** WICHTIG: Verwende normalisierte Werte für weighted sum! ***
            # *** OPTIMIERT: SAVI 30% (war 15%), NDWI 10% (war 5%) ***
            aerial_anomaly = (
                0.25 * nir_indices['crop_mark_potential'] +  # Reduziert von 30%
                0.20 * nir_indices['veg_stress'] +           # Reduziert von 25%
                0.30 * nir_indices['savi_fusion'] +          # ✅ Normalisiert! ERHÖHT von 15%!
                0.10 * soil_marks +                          # Reduziert von 15%
                0.05 * multiscale +                          # Reduziert von 10%
                0.10 * nir_indices['ndwi_fusion']            # ✅ Normalisiert! VERDOPPELT von 5%!
            )
            
            self.logger.info("  [PATTERN] Geometrische Muster...")
            # *** FIX v4.2.1: Reduziert von 85 auf 80 für mehr Sensitivität (NIR-Modus) ***
            threshold = np.percentile(aerial_anomaly, 80)
            binary = (aerial_anomaly > threshold).astype(np.uint8) * 255
            patterns = self.detect_geometric_patterns(binary)
            
            details = {
                'crop_marks': nir_indices['crop_mark_potential'],
                'veg_stress': nir_indices['veg_stress'],
                'soil_marks': soil_marks,
                'multiscale': multiscale,
                'rectangles': patterns['rectangles'],
                'circles': patterns['circles'],
                'ndvi': nir_indices['ndvi'],
                'evi': nir_indices['evi'],
                'savi': nir_indices['savi'],
                'ndwi': nir_indices['ndwi'],
                'has_nir': True,
                # *** FIX: Speichere auch originale NIR-Daten für Debug ***
                'nir_original': nir_data.get('nir'),
                'red_original': nir_data.get('red'),
                'green_original': nir_data.get('green'),
                'blue_original': nir_data.get('blue'),
                # *** Multi-Temporal Metriken (falls vorhanden) ***
                'persistence_score': nir_data.get('persistence_score'),
                'seasonal_contrast': nir_data.get('seasonal_contrast'),
                'high_confidence_mask': nir_data.get('high_confidence_mask'),
                'multi_temporal_mode': nir_data.get('mode', 'off')
            }
            
        elif aerial_rgb is not None:
            # *** STANDARD MODE: Nur RGB ***
            self.logger.info("  📷 [RGB MODE] Verwende Standard RGB-Analyse")
            
            self.logger.info("  [CROP] Crop Marks...")
            crop_data = self.detect_crop_marks(aerial_rgb)
            
            # Pseudo-NIR Fallback
            r = aerial_rgb[:, :, 0].astype(float)
            g = aerial_rgb[:, :, 1].astype(float)
            pseudo_nir = g
            red = r
            ndvi = (pseudo_nir - red) / (pseudo_nir + red + 1e-8)
            ndvi_norm = (ndvi - ndvi.min()) / (ndvi.max() - ndvi.min() + 1e-8)
            veg_stress = 1.0 - ndvi_norm
            
            self.logger.info("  [SOIL] Soil Marks...")
            soil_marks = self.detect_soil_marks(aerial_rgb)
            
            self.logger.info("  [MULTI] Multiskalen...")
            multiscale = self.multiscale_analysis(aerial_rgb)
            
            aerial_anomaly = (
                0.35 * crop_data['crop_marks'] +
                0.25 * veg_stress +
                0.20 * soil_marks +
                0.20 * multiscale
            )
            
            self.logger.info("  [PATTERN] Geometrische Muster...")
            # *** FIX v4.2.1: Reduziert von 85 auf 80 für mehr Sensitivität (RGB-Modus) ***
            threshold = np.percentile(aerial_anomaly, 80)
            binary = (aerial_anomaly > threshold).astype(np.uint8) * 255
            patterns = self.detect_geometric_patterns(binary)
            
            details = {
                'crop_marks': crop_data['crop_marks'],
                'veg_stress': veg_stress,
                'soil_marks': soil_marks,
                'multiscale': multiscale,
                'rectangles': patterns['rectangles'],
                'circles': patterns['circles'],
                'green_intensity': crop_data['green_intensity'],
                'exg_index': crop_data['exg_index'],
                'has_nir': False
            }
        else:
            raise ValueError("Entweder aerial_rgb oder nir_data muss angegeben werden!")
        
        return aerial_anomaly, details
    
    def advanced_lidar_analysis(self, lidar_image: np.ndarray, world_params: Dict = None) -> Tuple[np.ndarray, Dict]:
        """
        ÜBERARBEITETE LIDAR-Analyse mit wissenschaftlichen Verbesserungen.
        
        *** KRITISCHE FIXES: ***
        1. Resolution-aware kernel sizes
        2. Adaptive Threshold basierend auf lokaler Varianz
        3. Optimierte Feature-Gewichtung (archäologisch)
        4. Morphologische Features (Top-Hat/Black-Hat)
        5. Lokale Z-Score Analyse
        
        Args:
            lidar_image: LIDAR Höhenmodell
            world_params: World file Parameter für resolution-aware processing
        """
        try:
            if len(lidar_image.shape) == 3:
                gray = cv2.cvtColor(lidar_image, cv2.COLOR_RGB2GRAY)
            else:
                gray = lidar_image.copy()
            
            # *** FIX #1: Resolution-aware kernel sizes ***
            if world_params:
                pixel_size_deg = abs(world_params.get('pixel_size_x', 0.0001))
                meters_per_deg = 111320 * np.cos(np.radians(47))  # ~Mitteleuropa
                meters_per_pixel = pixel_size_deg * meters_per_deg
                
                # Archäologische Strukturen: 5-15m Radius
                kernel_small = max(5, int(5.0 / meters_per_pixel))  # 5m
                kernel_medium = max(7, int(10.0 / meters_per_pixel))  # 10m
                kernel_large = max(11, int(15.0 / meters_per_pixel))  # 15m
                
                self.logger.info(f"  [RESOLUTION] {meters_per_pixel:.2f}m/px")
                self.logger.info(f"  [KERNELS] Small:{kernel_small}, Medium:{kernel_medium}, Large:{kernel_large}")
            else:
                # Fallback
                kernel_small = 5
                kernel_medium = 11
                kernel_large = 21
                self.logger.warning("  [RESOLUTION] Keine world_params! Nutze Fallback-Kernels")
            
            # Stelle sicher dass Kernels ungerade sind
            kernel_small = kernel_small if kernel_small % 2 == 1 else kernel_small + 1
            kernel_medium = kernel_medium if kernel_medium % 2 == 1 else kernel_medium + 1
            kernel_large = kernel_large if kernel_large % 2 == 1 else kernel_large + 1
            
            # ===================================================================
            # FEATURE 1: RELIEF mit lokalem Z-Score
            # ===================================================================
            self.logger.info("  [LIDAR-RELIEF] Relief mit lokalem Z-Score...")
            local_mean = cv2.GaussianBlur(gray, (kernel_medium, kernel_medium), 0)
            
            # Lokale Standardabweichung
            gray_sq = gray.astype(float) ** 2
            local_mean_sq = cv2.GaussianBlur(gray_sq, (kernel_medium, kernel_medium), 0)
            local_var = local_mean_sq - (local_mean ** 2)
            local_std = np.sqrt(np.maximum(local_var, 0)) + 1e-8
            
            # Z-Score: Wie viele Standardabweichungen vom Mittel?
            relief = np.abs(gray.astype(float) - local_mean.astype(float))
            relief_zscore = relief / local_std
            relief_norm = np.clip(relief_zscore / 3.0, 0, 1)  # 3 sigma = 1.0
            
            # ===================================================================
            # FEATURE 2: GRADIENT (Neigungsänderungen)
            # ===================================================================
            gradient_x = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
            gradient_y = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
            gradient_magnitude = np.sqrt(gradient_x**2 + gradient_y**2)
            gradient_norm = (gradient_magnitude - gradient_magnitude.min()) / (gradient_magnitude.max() - gradient_magnitude.min() + 1e-8)
            
            # ===================================================================
            # FEATURE 3: EDGES (Strukturgrenzen)
            # ===================================================================
            edges = cv2.Canny(gray, 50, 150) / 255.0
            
            # ===================================================================
            # FEATURE 4: MORPHOLOGISCHE FEATURES (Hügel/Gräben)
            # ===================================================================
            self.logger.info("  [LIDAR-MORPH] Morphologische Features...")
            kernel_morph = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_small, kernel_small))
            
            # Top-Hat: Lokale Maxima (Hügel, Erhebungen)
            tophat = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, kernel_morph)
            tophat_norm = (tophat - tophat.min()) / (tophat.max() - tophat.min() + 1e-8)
            
            # Black-Hat: Lokale Minima (Gräben, Vertiefungen)
            blackhat = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, kernel_morph)
            blackhat_norm = (blackhat - blackhat.min()) / (blackhat.max() - blackhat.min() + 1e-8)
            
            # Kombiniere: Beide archäologisch relevant!
            morph_features = (tophat_norm + blackhat_norm) / 2.0
            
            # ===================================================================
            # FEATURE 5: MULTISCALE (mit mehreren Skalen)
            # ===================================================================
            self.logger.info("  [LIDAR-MULTI] Multiskalen...")
            multiscale_features = []
            
            for scale in [kernel_small, kernel_medium, kernel_large]:
                blurred = cv2.GaussianBlur(gray, (scale, scale), 0)
                log = cv2.Laplacian(blurred, cv2.CV_64F)
                log_abs = np.abs(log)
                log_norm = (log_abs - log_abs.min()) / (log_abs.max() - log_abs.min() + 1e-8)
                multiscale_features.append(log_norm)
            
            multiscale = np.mean(multiscale_features, axis=0)
            
            # ===================================================================
            # OPTIMIERTE GEWICHTUNG (archäologisch fundiert)
            # ===================================================================
            anomaly_map = (
                0.40 * relief_norm +      # Relief = WICHTIGSTER Indikator
                0.25 * edges +             # Strukturgrenzen
                0.15 * gradient_norm +     # Neigungen (korreliert mit Relief!)
                0.15 * morph_features +    # Hügel/Gräben
                0.05 * multiscale          # Textur
            )
            
            # ===================================================================
            # ADAPTIVE THRESHOLD basierend auf Datenverteilung
            # ===================================================================
            self.logger.info("  [THRESHOLD] Adaptive Schwellwertfindung...")
            
            # Berechne Statistiken
            mean_anom = np.mean(anomaly_map)
            std_anom = np.std(anomaly_map)
            
            # Adaptive Strategie:
            # - Wenn std hoch (stark variierendes Gelände): Höherer Threshold
            # - Wenn std niedrig (flaches Gelände): Niedrigerer Threshold
            # *** FIX v4.2.1: Reduziert Perzentile von 85-90 auf 75-85 ***
            
            if std_anom > 0.15:
                # Stark variierend → Otsu's Methode
                threshold = mean_anom + 1.5 * std_anom
                method = "Mean + 1.5σ (variierend)"
            elif std_anom > 0.10:
                # Mittel → 85. Perzentil (war 90)
                threshold = np.percentile(anomaly_map, 85)
                method = "85. Perzentil (mittel)"
            else:
                # Flach → 75. Perzentil (war 85)
                threshold = np.percentile(anomaly_map, 75)
                method = "75. Perzentil (flach)"
            
            self.logger.info(f"  [THRESHOLD] σ={std_anom:.3f}, Methode: {method}, Wert: {threshold:.3f}")
            
            binary = (anomaly_map > threshold).astype(np.uint8) * 255
            patterns = self.detect_geometric_patterns(binary)
            
            details = {
                'multiscale': multiscale,
                'relief': relief_norm,
                'gradient': gradient_norm,
                'edges': edges,
                'tophat': tophat_norm,
                'blackhat': blackhat_norm,
                'morph_features': morph_features,
                'rectangles': patterns['rectangles'],
                'circles': patterns['circles'],
                'threshold_method': method,
                'threshold_value': threshold
            }
            
            return anomaly_map, details
            
        except Exception as e:
            self.logger.error(f"Fehler bei LIDAR-Analyse: {e}")
            import traceback
            self.logger.debug(traceback.format_exc())
            h, w = lidar_image.shape[:2]
            empty = np.zeros((h, w), dtype=float)
            return empty, {
                'multiscale': empty, 'relief': empty, 'gradient': empty,
                'edges': empty, 'tophat': empty, 'blackhat': empty,
                'morph_features': empty, 'rectangles': empty, 'circles': empty,
                'threshold_method': 'fallback', 'threshold_value': 0.5
            }
    
    def advanced_historical_analysis(self, historical_image: np.ndarray, world_params: Dict = None) -> Tuple[np.ndarray, Dict]:
        """
        Historische Kartenanalyse mit resolution-aware Parametern.
        
        *** FIX v4.2.0: HoughLinesP jetzt resolution-aware! ***
        """
        try:
            if len(historical_image.shape) == 3:
                gray = cv2.cvtColor(historical_image, cv2.COLOR_RGB2GRAY)
            else:
                gray = historical_image.copy()
            
            # *** FIX: Resolution-aware Parameter für HoughLinesP ***
            if world_params:
                pixel_size_deg = abs(world_params.get('pixel_size_x', 0.0001))
                meters_per_deg = 111320 * np.cos(np.radians(47))  # ~Mitteleuropa
                meters_per_pixel = pixel_size_deg * meters_per_deg
                
                # Ziel: 50m Mindestlänge für Wege
                minLineLength = max(10, int(50.0 / meters_per_pixel))
                # Ziel: 15m maximale Lücke
                maxLineGap = max(3, int(15.0 / meters_per_pixel))
                
                self.logger.info(f"  [HIST-RESOLUTION] {meters_per_pixel:.2f}m/px")
                self.logger.info(f"  [HIST-HOUGH] minLineLength={minLineLength}px (~50m), maxLineGap={maxLineGap}px (~15m)")
            else:
                minLineLength = 30  # Fallback
                maxLineGap = 10
                self.logger.warning("  [HIST] Keine world_params! Nutze Fallback-Parameter")
            
            self.logger.info("  [HIST-PATHS] Wege...")
            
            # *** FIX v4.2.0: Adaptive Canny statt hardcoded (30, 100) ***
            # Otsu-basierte adaptive Schwellwerte
            median_val = np.median(gray)
            lower = int(max(0, 0.7 * median_val))
            upper = int(min(255, 1.3 * median_val))
            edges = cv2.Canny(gray, lower, upper)
            
            self.logger.info(f"  [HIST-CANNY] Adaptive threshold: {lower}/{upper}")
            
            lines = cv2.HoughLinesP(edges, 1, np.pi/180, threshold=50, 
                                    minLineLength=minLineLength, maxLineGap=maxLineGap)
            
            line_map = np.zeros_like(gray, dtype=float)
            if lines is not None:
                for line in lines:
                    x1, y1, x2, y2 = line[0]
                    cv2.line(line_map, (x1, y1), (x2, y2), 1.0, 2)
            
            self.logger.info("  [HIST-STRUCT] Strukturen...")
            contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            structure_map = np.zeros_like(gray, dtype=float)
            cv2.drawContours(structure_map, contours, -1, 1.0, 2)
            
            historical_features = 0.5 * line_map + 0.5 * structure_map
            
            details = {
                'paths': line_map,
                'structures': structure_map,
                'line_count': len(lines) if lines is not None else 0,
                'minLineLength': minLineLength,
                'maxLineGap': maxLineGap
            }
            
            return historical_features, details
        except Exception as e:
            self.logger.error(f"Fehler bei historischer Analyse: {e}")
            import traceback
            self.logger.debug(traceback.format_exc())
            h, w = historical_image.shape[:2]
            empty = np.zeros((h, w), dtype=float)
            return empty, {'paths': empty, 'structures': empty, 'line_count': 0,
                          'minLineLength': 30, 'maxLineGap': 10}
    
    def ultimate_fusion(self, lidar_features: np.ndarray,
                       historical_features: Optional[np.ndarray],
                       aerial_features: Optional[np.ndarray],
                       lidar_details: Dict,
                       hist_details: Optional[Dict],
                       aerial_details: Optional[Dict]) -> np.ndarray:
        """
        ULTIMATE FUSION mit NIR-Bonus.
        """
        h, w = lidar_features.shape
        
        lidar_norm = lidar_features / (lidar_features.max() + 1e-8)
        
        has_hist = historical_features is not None and hist_details is not None
        has_aerial = aerial_features is not None and aerial_details is not None
        has_nir = aerial_details.get('has_nir', False) if aerial_details else False
        
        if has_hist:
            if historical_features.shape != (h, w):
                historical_features = cv2.resize(historical_features, (w, h))
            hist_norm = historical_features / (historical_features.max() + 1e-8)
        else:
            hist_norm = np.zeros_like(lidar_norm)
            self.logger.warning("⚠️ Historische Daten fehlen")
        
        if has_aerial:
            if aerial_features.shape != (h, w):
                aerial_features = cv2.resize(aerial_features, (w, h))
            aerial_norm = aerial_features / (aerial_features.max() + 1e-8)
        else:
            aerial_norm = np.zeros_like(lidar_norm)
            self.logger.warning("⚠️ Luftbild-Daten fehlen")
        
        # Adaptive Gewichtung
        if has_hist and has_aerial:
            if has_nir:
                # PREMIUM: NIR bekommt HÖCHSTES Gewicht!
                # NIR ist die beste Quelle für Crop Marks!
                base_fusion = 0.25 * lidar_norm + 0.50 * aerial_norm + 0.25 * hist_norm
                self.logger.info("  [FUSION] 🌈 PREMIUM NIR-MODUS (50% NIR-Gewicht)!")
            else:
                # Standard RGB: LIDAR wichtiger
                base_fusion = 0.40 * lidar_norm + 0.30 * aerial_norm + 0.30 * hist_norm
                self.logger.info("  [FUSION] 3-Quellen-Modus (LIDAR dominant)")
        elif has_aerial:
            if has_nir:
                # NIR + LIDAR: NIR dominant!
                base_fusion = 0.40 * lidar_norm + 0.60 * aerial_norm
                self.logger.info("  [FUSION] 🌈 NIR+LIDAR (NIR dominant)")
            else:
                # RGB + LIDAR: LIDAR dominant
                base_fusion = 0.60 * lidar_norm + 0.40 * aerial_norm
                self.logger.info("  [FUSION] LIDAR+RGB (LIDAR dominant)")
        elif has_hist:
            base_fusion = 0.60 * lidar_norm + 0.40 * hist_norm
            self.logger.info("  [FUSION] 2-Quellen-Modus (LIDAR + Historie)")
        else:
            base_fusion = lidar_norm
            self.logger.warning("  [FUSION] 1-Quellen-Modus (nur LIDAR)")
        
        # Bonussysteme
        # *** OPTIMIERT: SAVI-LIDAR Bonus erhöht, NDWI-LIDAR Bonus hinzugefügt ***
        bonus_map = np.zeros_like(base_fusion)
        
        if has_aerial:
            crop_resized = cv2.resize(aerial_details['crop_marks'], (w, h))
            crop_lidar_bonus = crop_resized * lidar_norm
            
            # NIR-BONUS: Crop + LIDAR
            # *** FIX: Reduziert von 0.30 auf 0.20 für bessere Balance ***
            bonus_weight = 0.20 if has_nir else 0.15
            bonus_map += bonus_weight * crop_lidar_bonus
            
            if has_nir:
                self.logger.info("  [BONUS] 🌈 NIR Crop Mark Bonus: +20%")
            
            soil_resized = cv2.resize(aerial_details['soil_marks'], (w, h))
            soil_relief_bonus = soil_resized * lidar_details['relief']
            # *** FIX: Reduziert von 0.15 auf 0.10 ***
            bonus_map += 0.10 * soil_relief_bonus
            
            # *** OPTIMIERT: SAVI-LIDAR Bonus ***
            # *** FIX: Reduziert von 0.50 auf 0.30 für bessere Balance ***
            if has_nir and 'savi' in aerial_details:
                savi_resized = cv2.resize(aerial_details['savi'], (w, h))
                
                # *** FIX: SAVI ist 0-0.5, normalisiere erst, dann invertiere! ***
                # Niedrige SAVI = höherer Bonus (archäologisch interessant)
                savi_norm = self.normalize_for_fusion(savi_resized, (0.0, 0.5))  # → 0-1
                savi_inverted = 1.0 - savi_norm  # Jetzt korrektes 0-1
                savi_lidar_bonus = savi_inverted * lidar_details['relief']
                
                bonus_map += 0.30 * savi_lidar_bonus  # ✅ Reduziert von 0.50!
                self.logger.info("  [BONUS] 🌈 SAVI-LIDAR Bonus: +30% (invertiert)")
            
            # *** NEU: NDWI-LIDAR Bonus für Wassergräben! ***
            # *** FIX: Reduziert von 0.35 auf 0.20 ***
            if has_nir and 'ndwi' in aerial_details:
                ndwi_resized = cv2.resize(aerial_details['ndwi'], (w, h))
                
                # *** FIX: NDWI ist -1 bis +1, normalisiere hohe Werte ***
                # Hoher NDWI (> 0.4) = Wasserstruktur
                # Kombiniert mit LIDAR Relief = Graben!
                # Normalisiere 0.4-0.8 auf 0-1
                ndwi_high = np.clip((ndwi_resized - 0.4) / 0.4, 0, 1)
                ndwi_lidar_bonus = ndwi_high * lidar_details['relief']
                
                bonus_map += 0.20 * ndwi_lidar_bonus  # ✅ Reduziert von 0.35!
                self.logger.info("  [BONUS] 🌈 NDWI-LIDAR Bonus: +20% (Wassergräben)")
        
        if has_aerial and has_hist:
            stress_resized = cv2.resize(aerial_details['veg_stress'], (w, h))
            hist_struct_resized = cv2.resize(hist_details['structures'], (w, h))
            stress_hist_bonus = stress_resized * hist_struct_resized
            bonus_map += 0.15 * stress_hist_bonus
            
            lidar_rect = lidar_details['rectangles']
            aerial_rect_resized = cv2.resize(aerial_details['rectangles'], (w, h))
            triple_pattern_bonus = lidar_rect * aerial_rect_resized
            bonus_map += 0.25 * triple_pattern_bonus
        
        final_fusion = base_fusion + bonus_map
        final_fusion = np.clip(final_fusion, 0, 1)
        
        return final_fusion
    
    # [CLUSTERING UND WEITERE METHODEN BLEIBEN UNVERÄNDERT - Aus Platzgründen gekürzt]
    # Die restlichen Methoden (cluster_hotspots_optimized, analyze_hotspot_characteristics, etc.)
    # sind identisch zur vorherigen Version
    
    def cluster_hotspots_optimized(self, correlation_map: np.ndarray, 
                                   world_params: Dict,
                                   max_points: int = 5000) -> List[Dict]:
        """
        MEMORY-OPTIMIERTES Clustering.
        *** OPTIMIERT: Adaptive Schwellenwerte basierend auf Datenverteilung ***
        """
        try:
            # *** OPTIMIERT: Adaptive Schwellenwerte ***
            # *** FIX: Reduziert von 93-97% auf 85-92% für bessere Sensitivität ***
            std_dev = np.std(correlation_map)
            mean_val = np.mean(correlation_map)
            
            # Wenn hohe Varianz: Niedrigerer Schwellenwert OK
            # Wenn niedrige Varianz: Höherer Schwellenwert nötig
            if std_dev > 0.15:
                percentile = 85  # Mehr Variation → niedrigerer Schwellenwert (war 93)
            elif std_dev > 0.10:
                percentile = 88  # Normale Variation (war 95)
            else:
                percentile = 92  # Wenig Variation → höherer Schwellenwert (war 97)
            
            threshold = np.percentile(correlation_map, percentile)
            # *** FIX v4.2.1: Erweiterte Statistik-Ausgabe ***
            max_val = np.max(correlation_map)
            min_val = np.min(correlation_map)
            self.logger.info(f"  [ADAPTIVE] Std: {std_dev:.3f}, Mean: {mean_val:.3f}, Range: [{min_val:.3f}, {max_val:.3f}]")
            
            binary = (correlation_map > threshold).astype(np.uint8) * 255
            y_coords, x_coords = np.where(binary > 0)
            
            total_pixels = correlation_map.size
            min_candidate_pixels = max(50, int(0.0002 * total_pixels))  # Mindestens 0.02% oder 50 Pixel
            
            # *** NEU v4.2.1+: Dynamische Nachjustierung, falls zu wenige Kandidaten ***
            if len(x_coords) < min_candidate_pixels:
                self.logger.info(f"  [ADAPTIVE] Nur {len(x_coords)} Pixel über Schwelle (<{min_candidate_pixels}), lockere Perzentil...")
                
                best_binary = binary
                best_x, best_y = x_coords, y_coords
                best_threshold = threshold
                best_percentile = percentile
                
                for delta in (5, 10, 15):
                    cand_percentile = max(70, percentile - delta)
                    if cand_percentile == best_percentile:
                        continue
                    
                    cand_threshold = np.percentile(correlation_map, cand_percentile)
                    cand_binary = (correlation_map > cand_threshold).astype(np.uint8) * 255
                    cy, cx = np.where(cand_binary > 0)
                    
                    self.logger.info(f"    → {cand_percentile}. Perzentil: {len(cx)} Pixel")
                    
                    if len(cx) > len(best_x):
                        best_binary, best_x, best_y = cand_binary, cx, cy
                        best_threshold = cand_threshold
                        best_percentile = cand_percentile
                    
                    if len(cx) >= min_candidate_pixels:
                        break
                
                binary = best_binary
                x_coords, y_coords = best_x, best_y
                threshold = best_threshold
                percentile = best_percentile
            
            if len(x_coords) == 0:
                self.logger.warning("Keine Anomalien über Schwellenwert gefunden")
                self.logger.warning(f"  Möglicherweise zu strenger Schwellenwert. Versuchen Sie niedrigere Perzentile.")
                return []
            
            percent_above = (len(x_coords) / total_pixels) * 100
            self.logger.info(f"  [ADAPTIVE] Schwellenwert: {threshold:.3f} ({percentile}. Perzentil)")
            self.logger.info(f"  [INFO] {len(x_coords)} Pixel über Schwellenwert ({percent_above:.2f}% der Gesamtfläche)")
            
            if len(x_coords) > max_points:
                sample_rate = len(x_coords) // max_points
                x_coords = x_coords[::sample_rate]
                y_coords = y_coords[::sample_rate]
                self.logger.info(f"  [SAMPLE] Reduziert auf {len(x_coords)} Punkte (jeder {sample_rate}.)")
            
            points = np.column_stack([x_coords, y_coords])
            
            # *** BUGFIX #4: Resolution-aware DBSCAN Epsilon ***
            # Berechne pixel_size aus world_params
            pixel_size_deg = abs(world_params.get('pixel_size_x', 0.0001))
            
            # Angenommene Latitude ~47° (Mitteleuropa)
            meters_per_deg = 111320 * np.cos(np.radians(47))
            meters_per_pixel = pixel_size_deg * meters_per_deg
            
            # Ziel: eps = 50m in Pixeln
            if meters_per_pixel > 0:
                eps_pixels = max(5, int(50 / meters_per_pixel))  # Min 5 Pixel
            else:
                eps_pixels = 40  # Fallback
            
            self.logger.info(f"  [DBSCAN] eps={eps_pixels} Pixel (~50m, {meters_per_pixel:.2f}m/px)")
            
            # *** FIX v4.2.1: min_samples reduziert von 5 auf 3 ***
            # 5 = zu konservativ (zu wenige Hotspots)
            # 3 = ausgewogen (genug Sensitivität ohne zu viele False Positives)
            # Kombiniert mit verbesserten NDVI/NDWI-Filtern für Qualität
            min_samples = 3
            
            clustering = DBSCAN(eps=eps_pixels, min_samples=min_samples).fit(points)
            
            # *** FIX v4.2.1: Aktualisierte Beschreibung (3 ist ausgewogen, nicht konservativ) ***
            self.logger.info(f"  [DBSCAN] min_samples={min_samples} (ausgewogen)")
            
            hotspots = []
            unique_labels = set(clustering.labels_)
            num_clusters = len(unique_labels) - (1 if -1 in unique_labels else 0)
            self.logger.info(f"  [CLUSTERS] {num_clusters} Cluster gefunden")
            
            for cluster_id in unique_labels:
                if cluster_id == -1:
                    continue
                
                cluster_mask = clustering.labels_ == cluster_id
                cluster_points = points[cluster_mask]
                
                cx = int(cluster_points[:, 0].mean())
                cy = int(cluster_points[:, 1].mean())
                
                lon, lat = self.pixel_to_coords(cx, cy, world_params)
                
                # *** OPTIMIERT: Multi-Kriterien Confidence Score ***
                # Nicht nur Maximum (anfällig für Outliers), sondern gewichtete Kombination
                cluster_confidences = correlation_map[cluster_points[:, 1], cluster_points[:, 0]]
                
                max_val = float(np.max(cluster_confidences))
                mean_val = float(np.mean(cluster_confidences))
                median_val = float(np.median(cluster_confidences))
                p75_val = float(np.percentile(cluster_confidences, 75))
                
                # Gewichteter Score
                # Max allein ist unzuverlässig (Outliers)
                # Median allein unterschätzt starke Anomalien
                # Kombination ist optimal!
                base_confidence = (
                    0.40 * max_val +      # Höchster Wert
                    0.30 * p75_val +      # 75. Perzentil (konsistent hoch)
                    0.20 * mean_val +     # Durchschnitt
                    0.10 * median_val     # Median (robust gegen Outliers)
                )
                
                area = len(cluster_points)
                
                hotspots.append({
                    'lon': lon,
                    'lat': lat,
                    'pixel_x': cx,
                    'pixel_y': cy,
                    'confidence': base_confidence,  # Wird später mit Feature-Boosts erhöht
                    'area': area
                })
            
            hotspots.sort(key=lambda x: x['confidence'], reverse=True)
            
            del points, clustering, binary
            
            return hotspots
        except Exception as e:
            self.logger.error(f"Fehler beim Clustering: {e}")
            return []
    
    def analyze_hotspot_characteristics(self, px: int, py: int,
                                       lidar_details: Dict,
                                       hist_details: Optional[Dict],
                                       aerial_details: Optional[Dict],
                                       correlation_map: np.ndarray,
                                       world_params: Dict = None) -> Dict:
        """
        Analysiert Hotspot-Charakteristiken.
        
        *** FIX v4.2.0: Radius jetzt resolution-aware! ***
        
        Args:
            world_params: World file Parameter für resolution-aware radius
        """
        try:
            # *** FIX: Resolution-aware radius ***
            if world_params:
                pixel_size_deg = abs(world_params.get('pixel_size_x', 0.0001))
                meters_per_deg = 111320 * np.cos(np.radians(47))
                meters_per_pixel = pixel_size_deg * meters_per_deg
                
                # Ziel: 15m Radius für lokale Charakterisierung
                radius = max(5, int(15.0 / meters_per_pixel))
            else:
                radius = 15  # Fallback
            
            h, w = correlation_map.shape
            y1, y2 = max(0, py-radius), min(h, py+radius)
            x1, x2 = max(0, px-radius), min(w, px+radius)
            
            local_relief = float(lidar_details['relief'][y1:y2, x1:x2].mean())
            local_gradient = float(lidar_details['gradient'][y1:y2, x1:x2].mean())
            has_lidar_rect = bool(lidar_details['rectangles'][y1:y2, x1:x2].max() > 0)
            
            if hist_details:
                hist_paths = cv2.resize(hist_details['paths'], (w, h))
                hist_structures = cv2.resize(hist_details['structures'], (w, h))
                local_paths = float(hist_paths[y1:y2, x1:x2].mean())
                local_structures = float(hist_structures[y1:y2, x1:x2].mean())
            else:
                local_paths = 0.0
                local_structures = 0.0
            
            if aerial_details:
                crop_marks = cv2.resize(aerial_details['crop_marks'], (w, h))
                veg_stress = cv2.resize(aerial_details['veg_stress'], (w, h))
                soil_marks = cv2.resize(aerial_details['soil_marks'], (w, h))
                
                local_crop = float(crop_marks[y1:y2, x1:x2].mean())
                local_stress = float(veg_stress[y1:y2, x1:x2].mean())
                local_soil = float(soil_marks[y1:y2, x1:x2].mean())
                
                aerial_rect = cv2.resize(aerial_details['rectangles'], (w, h))
                has_aerial_rect = bool(aerial_rect[y1:y2, x1:x2].max() > 0)
                
                has_nir = aerial_details.get('has_nir', False)
                if has_nir and 'ndvi' in aerial_details:
                    ndvi_local = float(cv2.resize(aerial_details['ndvi'], (w, h))[y1:y2, x1:x2].mean())
                    # *** FIX: Extrahiere ALLE NIR-Indizes! ***
                    evi_local = float(cv2.resize(aerial_details['evi'], (w, h))[y1:y2, x1:x2].mean()) if 'evi' in aerial_details else 0.0
                    savi_local = float(cv2.resize(aerial_details['savi'], (w, h))[y1:y2, x1:x2].mean()) if 'savi' in aerial_details else 0.0
                    ndwi_local = float(cv2.resize(aerial_details['ndwi'], (w, h))[y1:y2, x1:x2].mean()) if 'ndwi' in aerial_details else 0.0
                    
                    # *** Multi-Temporal Metriken ***
                    if aerial_details.get('persistence_score') is not None:
                        persist_resized = cv2.resize(aerial_details['persistence_score'], (w, h))
                        persistence_local = float(persist_resized[y1:y2, x1:x2].mean())
                    else:
                        persistence_local = 0.0
                    
                    if aerial_details.get('seasonal_contrast') is not None:
                        contrast_resized = cv2.resize(aerial_details['seasonal_contrast'], (w, h))
                        contrast_local = float(contrast_resized[y1:y2, x1:x2].mean())
                    else:
                        contrast_local = 0.0
                    
                    if aerial_details.get('high_confidence_mask') is not None:
                        highconf_resized = cv2.resize(aerial_details['high_confidence_mask'], (w, h))
                        is_high_confidence = bool(highconf_resized[y1:y2, x1:x2].mean() > 0.5)
                    else:
                        is_high_confidence = False
                    
                    # *** KRITISCHER FIX: Filtere offensichtliche False Positives ***
                    # *** FIX v4.2.1: Weniger aggressive Filter für mehr Hotspots ***
                    
                    # 1. NDVI < -0.3 = Wasser/Schnee/Wolken → KEINE archäologische Fundstelle!
                    #    (Reduziert von -0.2 auf -0.3 für mehr Toleranz)
                    if ndvi_local < -0.3:
                        self.logger.debug(f"      [FILTER] Verwerfe Hotspot bei ({px},{py}): NDVI={ndvi_local:.3f} (Wasser/Schnee)")
                        return {
                            'invalid': True,
                            'reason': f'NDVI={ndvi_local:.3f} indicates water/snow/clouds',
                            'ndvi': ndvi_local
                        }
                    
                    # 2. NDVI > 0.90 = Dichter Wald → Unwahrscheinlich für archäologische Stätten
                    #    (Erhöht von 0.85 auf 0.90 - nur extrem dichte Wälder filtern)
                    #    (außer wenn LIDAR starke Anomalie zeigt)
                    if ndvi_local > 0.90 and local_relief < 0.3:
                        self.logger.debug(f"      [FILTER] Verwerfe Hotspot bei ({px},{py}): NDVI={ndvi_local:.3f} (Dichter Wald, keine LIDAR-Anomalie)")
                        return {
                            'invalid': True,
                            'reason': f'NDVI={ndvi_local:.3f} indicates dense forest without LIDAR anomaly',
                            'ndvi': ndvi_local
                        }
                    
                    # 3. NDWI > 0.8 = Permanent Wasser → Keine Fundstelle!
                    #    (Erhöht von 0.7 auf 0.8 - nur offensichtliche Wasserflächen)
                    #    (0.4-0.6 kann Graben sein, 0.6-0.8 kann alter Wassergraben sein)
                    if ndwi_local > 0.8:
                        self.logger.debug(f"      [FILTER] Verwerfe Hotspot bei ({px},{py}): NDWI={ndwi_local:.3f} (Permanent Wasser)")
                        return {
                            'invalid': True,
                            'reason': f'NDWI={ndwi_local:.3f} indicates permanent water body',
                            'ndwi': ndwi_local
                        }
                else:
                    ndvi_local = 0.0
                    evi_local = 0.0
                    savi_local = 0.0
                    ndwi_local = 0.0
                    persistence_local = 0.0
                    contrast_local = 0.0
                    is_high_confidence = False
            else:
                local_crop = 0.0
                local_stress = 0.0
                local_soil = 0.0
                has_aerial_rect = False
                has_nir = False
                ndvi_local = 0.0
                evi_local = 0.0
                savi_local = 0.0
                ndwi_local = 0.0
                persistence_local = 0.0
                contrast_local = 0.0
                is_high_confidence = False
            
            triple_confirmation = bool(
                has_lidar_rect and has_aerial_rect and local_structures > 0.3
            ) if aerial_details and hist_details else False
            
            return {
                'lidar_relief': local_relief,
                'lidar_gradient': local_gradient,
                'has_lidar_structure': has_lidar_rect,
                'hist_paths': local_paths,
                'hist_structures': local_structures,
                'crop_marks': local_crop,
                'veg_stress': local_stress,
                'soil_marks': local_soil,
                'has_aerial_structure': has_aerial_rect,
                'triple_confirmation': triple_confirmation,
                'has_nir_data': has_nir,
                'ndvi': ndvi_local,
                'evi': evi_local,      # *** FIX: EVI hinzugefügt ***
                'savi': savi_local,    # *** FIX: SAVI hinzugefügt ***
                'ndwi': ndwi_local,    # *** FIX: NDWI hinzugefügt ***
                # *** Multi-Temporal Metriken ***
                'persistence': persistence_local,        # 0-3 (Anzahl Jahre sichtbar)
                'seasonal_contrast': contrast_local,     # Sommer - Frühjahr
                'high_confidence': is_high_confidence    # Boolean
            }
        except Exception as e:
            self.logger.error(f"Fehler bei Hotspot-Charakterisierung: {e}")
            return {
                'lidar_relief': 0.0, 'lidar_gradient': 0.0, 'has_lidar_structure': False,
                'hist_paths': 0.0, 'hist_structures': 0.0,
                'crop_marks': 0.0, 'veg_stress': 0.0, 'soil_marks': 0.0,
                'has_aerial_structure': False, 'triple_confirmation': False,
                'has_nir_data': False, 'ndvi': 0.0, 'evi': 0.0, 'savi': 0.0, 'ndwi': 0.0,
                'persistence': 0.0, 'seasonal_contrast': 0.0, 'high_confidence': False
            }
    
    def generate_ultimate_explanation(self, chars: Dict, confidence: float) -> str:
        """Generiert Erklärung mit ALLEN NIR-Indizes."""
        explanations = []
        
        if chars['lidar_relief'] > 0.5:
            explanations.append("LIDAR: Signifikante Geländeanomalie")
        
        if chars['has_lidar_structure']:
            explanations.append("LIDAR: Geometrische Struktur")
        
        if chars['hist_structures'] > 0.3:
            explanations.append("HISTORIE: Gebäude in alter Karte")
        
        if chars['hist_paths'] > 0.3:
            explanations.append("HISTORIE: Alter Weg")
        
        if chars.get('has_nir_data', False):
            # *** FIX: ALLE NIR-Indizes interpretieren! ***
            if chars['crop_marks'] > 0.5:
                explanations.append("🌈 NIR: *** CROP MARKS - Echte Satellitenanomalien! ***")
            
            # NDVI Interpretation (Vegetation)
            if chars['ndvi'] < 0.3:
                explanations.append("🌈 NIR NDVI: Niedrige Vegetation (gestörter Bewuchs)")
            elif chars['ndvi'] > 0.7:
                explanations.append("🌈 NIR NDVI: Hohe Vegetation (dichter Bewuchs)")
            
            # EVI Interpretation (Enhanced Vegetation)
            if chars.get('evi', 0) < 0.3:
                explanations.append("🌈 NIR EVI: Schwache Biomasse (mögliche Anomalie)")
            elif chars.get('evi', 0) > 0.7:
                explanations.append("🌈 NIR EVI: Hohe Biomasse-Aktivität")
            
            # SAVI Interpretation (Soil-Adjusted - wichtig für Archäologie!)
            if chars.get('savi', 0) < 0.25:
                explanations.append("🌈 NIR SAVI: *** BODENANOMALIE - Ideal für vergrabene Strukturen! ***")
            elif chars.get('savi', 0) < 0.4:
                explanations.append("🌈 NIR SAVI: Geringer Bodenbewuchs (interessant)")
            
            # NDWI Interpretation (Wasser-Features)
            if chars.get('ndwi', 0) > 0.6:
                explanations.append("🌈 NIR NDWI: *** WASSERSTRUKTUR - Alter Graben/Kanal möglich! ***")
            elif chars.get('ndwi', 0) > 0.4:
                explanations.append("🌈 NIR NDWI: Erhöhte Feuchtigkeit (Struktur-Indikator)")
            
            # Kombinations-Analyse
            if chars.get('savi', 0) < 0.3 and chars['lidar_relief'] > 0.5:
                explanations.append("🌈🌈 *** PREMIUM COMBO: SAVI + LIDAR = Archäologische Hotspot! ***")
        else:
            if chars['crop_marks'] > 0.5:
                explanations.append("LUFTBILD: *** CROP MARKS - Bewuchsanomalien! ***")
        
        if chars['veg_stress'] > 0.5:
            explanations.append("LUFTBILD: Vegetationsstress")
        
        if chars['soil_marks'] > 0.4:
            explanations.append("LUFTBILD: Soil Marks - Bodenfarbunterschiede")
        
        if chars['has_aerial_structure']:
            explanations.append("LUFTBILD: Geometrische Muster")
        
        if chars['triple_confirmation']:
            explanations.append("*** DREIFACH-BESTÄTIGUNG: LIDAR + LUFTBILD + KARTE! ***")
        
        # *** Multi-Temporal Erklärungen ***
        persistence = chars.get('persistence', 0)
        if persistence >= 3:
            explanations.append("🚀🚀🚀 PERSISTENT: Anomalie über 3 JAHRE sichtbar! (MAXIMUM SICHERHEIT!)")
        elif persistence >= 2:
            explanations.append("🚀🚀 PERSISTENT: Anomalie über 2 Jahre sichtbar (SEHR SICHER!)")
        elif persistence >= 1:
            explanations.append("🚀 PERSISTENT: Anomalie 1 Jahr bestätigt")
        
        if chars.get('high_confidence', False):
            explanations.append("⭐⭐⭐ HIGH CONFIDENCE: Erfüllt ALLE Multi-Temporal Kriterien!")
        
        contrast = chars.get('seasonal_contrast', 1.0)
        ndvi_value = chars.get('ndvi', 0.5)
        
        # *** FIX: WISSENSCHAFTLICH KORREKTE NDVI-INTERPRETATION ***
        # Hoher NDVI (>0.7) + niedriger Contrast = Wald, KEIN Crop Mark!
        # NDVI < 0.2 = Boden/Fels, auch nicht archäologisch relevant
        # NDVI 0.2-0.5 + niedriger Contrast = ARCHÄOLOGISCH INTERESSANT!
        if ndvi_value > 0.7 and contrast < 0.15:
            explanations.append("⚠️ WARNUNG: Hoher NDVI + niedriger Contrast → Wahrscheinlich WALD, kein Crop Mark!")
        elif ndvi_value < 0.2:
            explanations.append(f"ℹ️ INFO: Sehr niedriger NDVI ({ndvi_value:.2f}) → Boden/Fels-Bereich")
        elif 0.2 <= ndvi_value < 0.5 and contrast < 0.15:
            # Perfekter Bereich für archäologische Anomalien!
            explanations.append(f"🌱 SEASONAL: Geringes Wachstum (NDVI={ndvi_value:.2f}, Δ={contrast:.2f}) → Struktur behindert Vegetation!")
        
        if persistence >= 2 and chars.get('high_confidence', False):
            explanations.append("💎💎💎 *** ULTIMATE DETECTION: Multi-Jahr + High Confidence = ARCHÄOLOGISCHER FUND! ***")
        
        if chars['crop_marks'] > 0.5 and chars['lidar_relief'] > 0.5:
            marker = "🌈 " if chars.get('has_nir_data', False) else ""
            explanations.append(f"{marker}*** PREMIUM: Crop Marks + LIDAR = Vergrabene Struktur! ***")
        
        if not explanations:
            explanations.append("Multi-Source-Anomalie")
        
        return " * " + "\n * ".join(explanations)
    
    def save_heatmap(self, correlation_map: np.ndarray, output_path: str) -> bool:
        """Speichert Heatmap."""
        try:
            heatmap_norm = (correlation_map * 255).astype(np.uint8)
            heatmap_colored = cv2.applyColorMap(heatmap_norm, cv2.COLORMAP_JET)
            cv2.imwrite(output_path, heatmap_colored)
            self.logger.info(f"[HEATMAP] Gespeichert: {output_path}")
            return True
        except Exception as e:
            self.logger.error(f"Fehler beim Speichern der Heatmap: {e}")
            return False
    
    def save_nir_debug_outputs(self, aerial_details: Dict, world_params: Dict, 
                               output_dir: str = ".", lidar_shape: tuple = None) -> Dict[str, str]:
        """
        *** DEBUG FUNKTION ***
        Speichert alle NIR-Indizes als PNG und KML für visuelle Überprüfung.
        
        *** WICHTIG: Resized zu LIDAR-Dimensionen für korrekte Ausrichtung! ***
        
        Args:
            aerial_details: Aerial analysis details mit NIR-Daten
            world_params: LIDAR World-File Parameter (für korrekte Georeferenzierung!)
            output_dir: Ausgabeverzeichnis für Debug-Dateien
            lidar_shape: (height, width) des LIDAR-Bildes für Resize
        
        Returns:
            Dict mit Pfaden zu allen generierten Dateien
        """
        if not aerial_details or not aerial_details.get('has_nir', False):
            self.logger.warning("⚠️ Keine NIR-Daten verfügbar für Debug-Ausgabe")
            return {}
        
        try:
            self.logger.info("\n🔍 [DEBUG] Erstelle NIR Debug-Ausgaben...")
            
            # *** FIX: Verwende Original NIR-Daten in voller Auflösung! ***
            nir_orig = aerial_details.get('nir_original')
            red_orig = aerial_details.get('red_original')
            green_orig = aerial_details.get('green_original')
            blue_orig = aerial_details.get('blue_original')
            
            if nir_orig is None or red_orig is None:
                self.logger.warning("⚠️ Original NIR-Daten nicht verfügbar, verwende resized Daten")
                # Fallback zu resized Daten
                indices = {
                    'ndvi': aerial_details.get('ndvi'),
                    'evi': aerial_details.get('evi'),
                    'savi': aerial_details.get('savi'),
                    'ndwi': aerial_details.get('ndwi')
                }
            else:
                # *** NEU: Berechne Indices aus Original NIR-Daten ***
                self.logger.info("  ✅ Verwende Original NIR-Daten in voller Auflösung!")
                self.logger.info(f"  [DEBUG] NIR Shape (Original): {nir_orig.shape}")
                
                indices = {
                    'ndvi': self.calculate_true_ndvi(nir_orig, red_orig),
                    'savi': self.calculate_savi(nir_orig, red_orig),
                    'ndwi': self.calculate_ndwi(nir_orig, green_orig) if green_orig is not None else None
                }
                
                # EVI braucht auch Blue
                if blue_orig is not None:
                    indices['evi'] = self.calculate_evi(nir_orig, red_orig, blue_orig)
                else:
                    indices['evi'] = None
            
            # *** KRITISCHER FIX: Resize alle Indices zu LIDAR-Dimensionen! ***
            if lidar_shape is not None:
                target_h, target_w = lidar_shape[:2]
                self.logger.info(f"  🔧 [RESIZE] Resize Debug-Dateien zu LIDAR-Dimensionen: {target_w}x{target_h}")
                
                resized_indices = {}
                for name, data in indices.items():
                    if data is not None:
                        original_shape = data.shape
                        # Resize to LIDAR dimensions (width, height in cv2.resize!)
                        resized = cv2.resize(data, (target_w, target_h), interpolation=cv2.INTER_LINEAR)
                        resized_indices[name] = resized
                        self.logger.info(f"     {name.upper()}: {original_shape} → {resized.shape}")
                    else:
                        resized_indices[name] = None
                
                indices = resized_indices
            else:
                self.logger.warning("  ⚠️ WARNUNG: Keine LIDAR-Dimensionen angegeben, Debug-Dateien haben möglicherweise falsche Größe!")
            
            # *** DEBUG: Zeige World-Parameter ***
            self.logger.info(f"  [DEBUG] World-Params (LIDAR): {world_params}")
            
            output_files = {}
            
            # *** DEBUG: Zeige finale Größen ***
            for name, data in indices.items():
                if data is not None:
                    self.logger.info(f"  [DEBUG] {name.upper()} Shape (Final): {data.shape}")
            
            # Für jeden Index: PNG + KML erstellen
            for idx_name, idx_data in indices.items():
                if idx_data is None:
                    continue
                
                # 1. PNG mit Colormap erstellen
                png_path = os.path.join(output_dir, f"{idx_name}_debug.png")
                
                # *** WICHTIG: Jeder Index hat eigene Range! ***
                if idx_name == 'ndvi':
                    # NDVI: -1 bis +1, custom Blue->Red Colormap
                    rgb = self._ndvi_to_rgb(idx_data)
                    idx_colored = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
                    
                    legend_height = 60
                    legend_width = idx_colored.shape[1]
                    legend = np.zeros((legend_height, legend_width, 3), dtype=np.uint8)
                    ndvi_range = np.linspace(-0.2, 0.9, legend_width).astype('float32')
                    ndvi_range_2d = np.tile(ndvi_range, (legend_height, 1))
                    legend_rgb = self._ndvi_to_rgb(ndvi_range_2d)
                    legend = cv2.cvtColor(legend_rgb, cv2.COLOR_RGB2BGR)
                    
                elif idx_name == 'evi':
                    # EVI: -1 bis +1, normalisiere für JET
                    idx_norm = self.normalize_for_fusion(idx_data, (-1.0, 1.0))
                    idx_norm_uint8 = (idx_norm * 255).astype(np.uint8)
                    idx_colored = cv2.applyColorMap(idx_norm_uint8, cv2.COLORMAP_JET)
                    
                    legend_height = 60
                    legend = np.zeros((legend_height, idx_colored.shape[1], 3), dtype=np.uint8)
                    gradient = np.linspace(0, 255, legend.shape[1]).astype(np.uint8)
                    for i in range(legend_height):
                        legend[i, :] = cv2.applyColorMap(gradient[np.newaxis, :], cv2.COLORMAP_JET)[0]
                    
                elif idx_name == 'savi':
                    # SAVI: 0 bis ~0.5, normalisiere für JET
                    idx_norm = self.normalize_for_fusion(idx_data, (0.0, 0.5))
                    idx_norm_uint8 = (idx_norm * 255).astype(np.uint8)
                    idx_colored = cv2.applyColorMap(idx_norm_uint8, cv2.COLORMAP_JET)
                    
                    legend_height = 60
                    legend = np.zeros((legend_height, idx_colored.shape[1], 3), dtype=np.uint8)
                    gradient = np.linspace(0, 255, legend.shape[1]).astype(np.uint8)
                    for i in range(legend_height):
                        legend[i, :] = cv2.applyColorMap(gradient[np.newaxis, :], cv2.COLORMAP_JET)[0]
                    
                elif idx_name == 'ndwi':
                    # NDWI: -1 bis +1, normalisiere für JET
                    idx_norm = self.normalize_for_fusion(idx_data, (-1.0, 1.0))
                    idx_norm_uint8 = (idx_norm * 255).astype(np.uint8)
                    idx_colored = cv2.applyColorMap(idx_norm_uint8, cv2.COLORMAP_JET)
                    
                    legend_height = 60
                    legend = np.zeros((legend_height, idx_colored.shape[1], 3), dtype=np.uint8)
                    gradient = np.linspace(0, 255, legend.shape[1]).astype(np.uint8)
                    for i in range(legend_height):
                        legend[i, :] = cv2.applyColorMap(gradient[np.newaxis, :], cv2.COLORMAP_JET)[0]
                else:
                    # Andere: Nehme an 0-1 Range
                    idx_norm = (idx_data * 255).astype(np.uint8)
                    idx_colored = cv2.applyColorMap(idx_norm, cv2.COLORMAP_JET)
                    
                    legend_height = 60
                    legend = np.zeros((legend_height, idx_colored.shape[1], 3), dtype=np.uint8)
                    gradient = np.linspace(0, 255, legend.shape[1]).astype(np.uint8)
                    for i in range(legend_height):
                        legend[i, :] = cv2.applyColorMap(gradient[np.newaxis, :], cv2.COLORMAP_JET)[0]
                
                # Text auf Legende
                cv2.putText(legend, f"{idx_name.upper()}", (10, 20), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
                cv2.putText(legend, f"Min: {idx_data.min():.3f}", (10, 45), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
                cv2.putText(legend, f"Max: {idx_data.max():.3f}", 
                           (legend.shape[1] - 150, 45), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
                
                # Zeige erwartete Range für jeden Index
                if idx_name == 'ndvi':
                    range_text = "Range: -1.0 to +1.0"
                elif idx_name == 'evi':
                    range_text = "Range: -1.0 to +1.0"
                elif idx_name == 'savi':
                    range_text = "Range: 0.0 to ~0.5"
                elif idx_name == 'ndwi':
                    range_text = "Range: -1.0 to +1.0"
                else:
                    range_text = "Range: 0.0 to 1.0"
                    
                cv2.putText(legend, range_text, 
                           (legend.shape[1] // 2 - 100, legend_height - 10), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.35, (255, 255, 255), 1)
                
                # Kombiniere Bild + Legende
                debug_img = np.vstack([idx_colored, legend])
                
                cv2.imwrite(png_path, debug_img)
                output_files[f'{idx_name}_png'] = png_path
                self.logger.info(f"  ✅ {idx_name.upper()} PNG: {png_path}")
                
                # 2. KML mit Grid-Overlay erstellen
                kml_path = os.path.join(output_dir, f"{idx_name}_debug.kml")
                self.create_nir_debug_kml(idx_data, idx_name, world_params, kml_path)
                output_files[f'{idx_name}_kml'] = kml_path
                self.logger.info(f"  ✅ {idx_name.upper()} KML: {kml_path}")
            
            # 3. Erstelle kombinierte RGB-Visualisierung
            rgb_debug_path = os.path.join(output_dir, "nir_rgb_debug.png")
            self.create_nir_rgb_visualization(indices, rgb_debug_path)
            output_files['rgb_debug'] = rgb_debug_path
            self.logger.info(f"  ✅ RGB Debug: {rgb_debug_path}")
            
            # 4. Erstelle Statistik-Bericht
            stats_path = os.path.join(output_dir, "nir_statistics.txt")
            self.create_nir_statistics(indices, stats_path)
            output_files['statistics'] = stats_path
            self.logger.info(f"  ✅ Statistiken: {stats_path}")
            
            # *** NEU v4.2.0: Multi-Temporal Debug-Ausgaben! ***
            if aerial_details.get('mode') == 'ultimate' or aerial_details.get('multi_temporal_mode') == 'ultimate':
                self.logger.info("\n🚀 [MULTI-TEMPORAL] Erstelle zusätzliche Debug-Ausgaben...")
                
                # 5. Persistence Debug
                persistence_score = aerial_details.get('persistence_score')
                if persistence_score is not None:
                    # *** FIX: Resize zu LIDAR-Dimensionen! ***
                    if lidar_shape is not None:
                        target_h, target_w = lidar_shape[:2]
                        persistence_score = cv2.resize(persistence_score, (target_w, target_h), interpolation=cv2.INTER_LINEAR)
                        self.logger.info(f"     PERSISTENCE: Resized zu {persistence_score.shape}")
                    
                    persist_png = os.path.join(output_dir, "persistence_debug.png")
                    persist_kml = os.path.join(output_dir, "persistence_debug.kml")
                    
                    self.create_multitemporal_debug_png(
                        persistence_score, 
                        'Persistence',
                        persist_png,
                        vmin=0, vmax=3,
                        description="Years visible (0-3)"
                    )
                    self.create_nir_debug_kml(persistence_score, 'persistence', world_params, persist_kml)
                    
                    output_files['persistence_png'] = persist_png
                    output_files['persistence_kml'] = persist_kml
                    self.logger.info(f"  ✅ PERSISTENCE PNG/KML erstellt")
                
                # 6. Seasonal Contrast Debug
                seasonal_contrast = aerial_details.get('seasonal_contrast')
                if seasonal_contrast is not None:
                    # *** FIX: Resize zu LIDAR-Dimensionen! ***
                    if lidar_shape is not None:
                        target_h, target_w = lidar_shape[:2]
                        seasonal_contrast = cv2.resize(seasonal_contrast, (target_w, target_h), interpolation=cv2.INTER_LINEAR)
                        self.logger.info(f"     SEASONAL_CONTRAST: Resized zu {seasonal_contrast.shape}")
                    
                    contrast_png = os.path.join(output_dir, "seasonal_contrast_debug.png")
                    contrast_kml = os.path.join(output_dir, "seasonal_contrast_debug.kml")
                    
                    self.create_multitemporal_debug_png(
                        seasonal_contrast,
                        'Seasonal Contrast',
                        contrast_png,
                        vmin=-0.3, vmax=0.3,
                        description="Summer - Spring NDVI"
                    )
                    self.create_nir_debug_kml(seasonal_contrast, 'seasonal_contrast', world_params, contrast_kml)
                    
                    output_files['seasonal_contrast_png'] = contrast_png
                    output_files['seasonal_contrast_kml'] = contrast_kml
                    self.logger.info(f"  ✅ SEASONAL CONTRAST PNG/KML erstellt")
                
                # 7. High Confidence Debug
                high_confidence = aerial_details.get('high_confidence_mask')
                if high_confidence is not None:
                    # *** FIX: Resize zu LIDAR-Dimensionen! ***
                    if lidar_shape is not None:
                        target_h, target_w = lidar_shape[:2]
                        high_confidence = cv2.resize(high_confidence, (target_w, target_h), interpolation=cv2.INTER_NEAREST)  # NEAREST für Binary-Maske!
                        self.logger.info(f"     HIGH_CONFIDENCE: Resized zu {high_confidence.shape}")
                    
                    highconf_png = os.path.join(output_dir, "high_confidence_debug.png")
                    highconf_kml = os.path.join(output_dir, "high_confidence_debug.kml")
                    
                    self.create_multitemporal_debug_png(
                        high_confidence,
                        'High Confidence',
                        highconf_png,
                        vmin=0, vmax=1,
                        description="High confidence sites (binary)"
                    )
                    self.create_nir_debug_kml(high_confidence, 'high_confidence', world_params, highconf_kml)
                    
                    output_files['high_confidence_png'] = highconf_png
                    output_files['high_confidence_kml'] = highconf_kml
                    self.logger.info(f"  ✅ HIGH CONFIDENCE PNG/KML erstellt")
                
                self.logger.info(f"🚀 [MULTI-TEMPORAL] {3} zusätzliche Debug-Dateien erstellt!")
            
            self.logger.info(f"🔍 [DEBUG] {len(output_files)} Dateien erstellt")
            return output_files
            
        except Exception as e:
            self.logger.error(f"❌ Fehler bei Debug-Ausgabe: {e}")
            import traceback
            self.logger.debug(traceback.format_exc())
            return {}
    
    def create_nir_debug_kml(self, index_data: np.ndarray, index_name: str, 
                            world_params: Dict, output_path: str) -> bool:
        """Erstellt KML mit Ground-Overlay für NIR-Index."""
        try:
            # Berechne Bounds
            h, w = index_data.shape
            
            # Ecken berechnen
            north = world_params['upper_left_y']
            south = world_params['upper_left_y'] + h * world_params['pixel_size_y']
            west = world_params['upper_left_x']
            east = world_params['upper_left_x'] + w * world_params['pixel_size_x']
            
            # Erstelle temporäres PNG für Ground-Overlay
            temp_png = output_path.replace('.kml', '_overlay.png')
            idx_norm = (index_data * 255).astype(np.uint8)
            idx_colored = cv2.applyColorMap(idx_norm, cv2.COLORMAP_JET)
            cv2.imwrite(temp_png, idx_colored)
            
            # KML erstellen
            kml = ET.Element('kml', xmlns='http://www.opengis.net/kml/2.2')
            document = ET.SubElement(kml, 'Document')
            
            name = ET.SubElement(document, 'name')
            name.text = f"{index_name.upper()} Debug Overlay"
            
            desc = ET.SubElement(document, 'description')
            desc.text = f"""
            NIR Index: {index_name.upper()}
            Min: {index_data.min():.3f}
            Max: {index_data.max():.3f}
            Mean: {index_data.mean():.3f}
            Std: {index_data.std():.3f}
            """
            
            # Ground Overlay
            overlay = ET.SubElement(document, 'GroundOverlay')
            overlay_name = ET.SubElement(overlay, 'name')
            overlay_name.text = f"{index_name.upper()} Heatmap"
            
            icon = ET.SubElement(overlay, 'Icon')
            href = ET.SubElement(icon, 'href')
            href.text = os.path.basename(temp_png)
            
            lat_lon_box = ET.SubElement(overlay, 'LatLonBox')
            ET.SubElement(lat_lon_box, 'north').text = str(north)
            ET.SubElement(lat_lon_box, 'south').text = str(south)
            ET.SubElement(lat_lon_box, 'east').text = str(east)
            ET.SubElement(lat_lon_box, 'west').text = str(west)
            
            # Sample-Points für Werte-Check (Grid 5x5)
            folder = ET.SubElement(document, 'Folder')
            folder_name = ET.SubElement(folder, 'name')
            folder_name.text = f"{index_name.upper()} Sample Points"
            
            grid_points = 5
            for i in range(grid_points):
                for j in range(grid_points):
                    px = int(w * i / (grid_points - 1)) if i < grid_points - 1 else w - 1
                    py = int(h * j / (grid_points - 1)) if j < grid_points - 1 else h - 1
                    
                    lon, lat = self.pixel_to_coords(px, py, world_params)
                    value = float(index_data[py, px])
                    
                    placemark = ET.SubElement(folder, 'Placemark')
                    pm_name = ET.SubElement(placemark, 'name')
                    pm_name.text = f"{value:.3f}"
                    
                    pm_desc = ET.SubElement(placemark, 'description')
                    pm_desc.text = f"{index_name.upper()}: {value:.3f}<br/>Pixel: ({px}, {py})"
                    
                    point = ET.SubElement(placemark, 'Point')
                    coords = ET.SubElement(point, 'coordinates')
                    coords.text = f"{lon},{lat},0"
            
            # Speichern
            xml_str = minidom.parseString(ET.tostring(kml)).toprettyxml(indent='  ')
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(xml_str)
            
            return True
            
        except Exception as e:
            self.logger.error(f"Fehler beim KML-Export: {e}")
            return False
    
    def create_nir_rgb_visualization(self, indices: Dict, output_path: str) -> bool:
        """Erstellt RGB-Komposit aus NIR-Indizes."""
        try:
            ndvi = indices.get('ndvi')
            evi = indices.get('evi')
            savi = indices.get('savi')
            
            if ndvi is None or evi is None or savi is None:
                return False
            
            # RGB-Komposit: R=NDVI, G=EVI, B=SAVI
            rgb = np.stack([
                (ndvi * 255).astype(np.uint8),
                (evi * 255).astype(np.uint8),
                (savi * 255).astype(np.uint8)
            ], axis=2)
            
            # BGR für OpenCV
            bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
            
            cv2.imwrite(output_path, bgr)
            return True
            
        except Exception as e:
            self.logger.error(f"Fehler bei RGB-Visualisierung: {e}")
            return False
    
    def create_multitemporal_debug_png(self, data: np.ndarray, title: str, 
                                       output_path: str, vmin: float, vmax: float,
                                       description: str = "") -> bool:
        """
        Erstellt Debug-PNG für Multi-Temporal Metriken.
        
        *** NEU v4.2.0: Für Persistence, Seasonal Contrast, High Confidence ***
        
        Args:
            data: 2D Array mit Metrik-Daten
            title: Titel der Metrik
            output_path: Pfad für PNG
            vmin, vmax: Value range für Normalisierung
            description: Zusätzliche Beschreibung
        """
        try:
            # Normalisiere auf 0-255
            data_norm = np.clip((data - vmin) / (vmax - vmin), 0, 1)
            data_uint8 = (data_norm * 255).astype(np.uint8)
            
            # Colormap anwenden
            data_colored = cv2.applyColorMap(data_uint8, cv2.COLORMAP_JET)
            
            # Erstelle Legende
            legend_height = 80
            legend_width = data_colored.shape[1]
            legend = np.zeros((legend_height, legend_width, 3), dtype=np.uint8)
            
            # Gradient
            gradient = np.linspace(0, 255, legend_width).astype(np.uint8)
            for i in range(legend_height - 30):
                legend[i, :] = cv2.applyColorMap(gradient[np.newaxis, :], cv2.COLORMAP_JET)[0]
            
            # Text
            cv2.putText(legend, title.upper(), (10, legend_height - 60), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
            cv2.putText(legend, f"Min: {data.min():.3f}", (10, legend_height - 35), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
            cv2.putText(legend, f"Max: {data.max():.3f}", 
                       (legend_width - 150, legend_height - 35), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
            
            if description:
                cv2.putText(legend, description, 
                           (legend_width // 2 - 100, legend_height - 10), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)
            
            # Kombiniere
            debug_img = np.vstack([data_colored, legend])
            
            cv2.imwrite(output_path, debug_img)
            return True
            
        except Exception as e:
            self.logger.error(f"Fehler bei Multi-Temporal Debug PNG: {e}")
            return False
    
    def create_nir_statistics(self, indices: Dict, output_path: str) -> bool:
        """Erstellt detaillierte Statistik-Datei."""
        try:
            with open(output_path, 'w') as f:
                f.write("="*60 + "\n")
                f.write("NIR INDIZES - DETAILLIERTE STATISTIKEN\n")
                f.write("="*60 + "\n\n")
                
                for idx_name, idx_data in indices.items():
                    if idx_data is None:
                        continue
                    
                    f.write(f"--- {idx_name.upper()} ---\n")
                    f.write(f"  Min:        {idx_data.min():.6f}\n")
                    f.write(f"  Max:        {idx_data.max():.6f}\n")
                    f.write(f"  Mean:       {idx_data.mean():.6f}\n")
                    f.write(f"  Median:     {np.median(idx_data):.6f}\n")
                    f.write(f"  Std Dev:    {idx_data.std():.6f}\n")
                    f.write(f"  Percentiles:\n")
                    f.write(f"    10%:      {np.percentile(idx_data, 10):.6f}\n")
                    f.write(f"    25%:      {np.percentile(idx_data, 25):.6f}\n")
                    f.write(f"    50%:      {np.percentile(idx_data, 50):.6f}\n")
                    f.write(f"    75%:      {np.percentile(idx_data, 75):.6f}\n")
                    f.write(f"    90%:      {np.percentile(idx_data, 90):.6f}\n")
                    f.write(f"    95%:      {np.percentile(idx_data, 95):.6f}\n")
                    f.write(f"    99%:      {np.percentile(idx_data, 99):.6f}\n")
                    f.write("\n")
                
                f.write("="*60 + "\n")
                f.write("INTERPRETATION:\n")
                f.write("="*60 + "\n\n")
                
                ndvi = indices.get('ndvi')
                if ndvi is not None:
                    f.write("NDVI (Normalized Difference Vegetation Index):\n")
                    f.write("  - Normalbereich: 0.4 - 0.6 für normalen Bewuchs\n")
                    f.write("  - < 0.3: Schwache Vegetation (interessant!)\n")
                    f.write("  - > 0.7: Dichte Vegetation\n")
                    f.write(f"  - Dein Gebiet: {ndvi.mean():.3f} (Mean)\n\n")
                
                savi = indices.get('savi')
                if savi is not None:
                    f.write("SAVI (Soil-Adjusted Vegetation Index):\n")
                    f.write("  - *** WICHTIGSTER für Archäologie! ***\n")
                    f.write("  - < 0.25: PREMIUM für vergrabene Strukturen!\n")
                    f.write("  - < 0.40: Interessant für Surveys\n")
                    f.write(f"  - Dein Gebiet: {savi.mean():.3f} (Mean)\n")
                    low_savi_percent = (savi < 0.25).sum() / savi.size * 100
                    f.write(f"  - {low_savi_percent:.1f}% des Gebiets hat SAVI < 0.25\n\n")
                
                evi = indices.get('evi')
                if evi is not None:
                    f.write("EVI (Enhanced Vegetation Index):\n")
                    f.write("  - Verbessert bei hoher Biomasse\n")
                    f.write("  - < 0.3: Schwache Biomasse\n")
                    f.write(f"  - Dein Gebiet: {evi.mean():.3f} (Mean)\n\n")
                
                ndwi = indices.get('ndwi')
                if ndwi is not None:
                    f.write("NDWI (Normalized Difference Water Index):\n")
                    f.write("  - > 0.7: Wasserstrukturen (Gräben/Kanäle) - WISSENSCHAFTLICH KORRIGIERT\n")
                    f.write("  - > 0.4: Erhöhte Feuchtigkeit\n")
                    f.write(f"  - Dein Gebiet: {ndwi.mean():.3f} (Mean)\n")
                    water_percent = (ndwi > 0.7).sum() / ndwi.size * 100
                    f.write(f"  - {water_percent:.1f}% des Gebiets hat NDWI > 0.7\n\n")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Fehler bei Statistik-Export: {e}")
            return False
    
    def print_top_findings(self, hotspots: List[Dict], top_n: int = 10) -> None:
        """Druckt Top N mit ALLEN NIR-Indizes."""
        print("\n" + "="*80)
        print("*** TOP {0} FUNDSTELLEN (ULTIMATE + NIR) ***".format(min(top_n, len(hotspots))))
        print("="*80)
        
        for i, site in enumerate(hotspots[:top_n], 1):
            print("\n+--- FUNDSTELLE #{0}".format(i))
            print("|  Lat: {0:.6f} | Lon: {1:.6f}".format(site['lat'], site['lon']))
            print("|  Konfidenz: {0:.1%}".format(site['confidence']))
            
            # *** OPTIMIERT: Zeige Feature-Boost an ***
            if 'feature_boost' in site and site['feature_boost'] > 1.0:
                boost_percent = (site['feature_boost'] - 1.0) * 100
                print("|  📈 Feature-Boost: +{0:.0f}% (Base: {1:.1%})".format(
                    boost_percent, site.get('base_confidence', site['confidence'])))
            
            if 'characteristics' in site:
                chars = site['characteristics']
                nir_marker = "🌈 " if chars.get('has_nir_data', False) else ""
                print("|  {0}[LIDAR] Relief: {1:.1%} | Struktur: {2}".format(
                    nir_marker, chars['lidar_relief'], "JA" if chars['has_lidar_structure'] else "NEIN"))
                print("|  [LUFTBILD] Crop: {0:.1%} | Stress: {1:.1%}".format(
                    chars['crop_marks'], chars['veg_stress']))
                
                # *** FIX: ALLE NIR-Indizes ausgeben! ***
                if chars.get('has_nir_data', False):
                    print("|  🌈 [NIR-INDIZES]")
                    print("|     NDVI: {0:.3f} (Vegetation)".format(chars['ndvi']))
                    print("|     EVI:  {0:.3f} (Enhanced Veg)".format(chars.get('evi', 0)))
                    print("|     SAVI: {0:.3f} (Boden-adjustiert) ***".format(chars.get('savi', 0)))
                    print("|     NDWI: {0:.3f} (Wasser-Features)".format(chars.get('ndwi', 0)))
                
                print("|  [3-FACH] {0}".format("*** JA ***" if chars['triple_confirmation'] else "NEIN"))
                print("|")
                for line in site['explanation'].split('\n'):
                    print("|  {0}".format(line))
            
            priority = "*** ULTRA ***" if site['confidence'] > 0.8 else "** HOCH **"
            print("|  PRIORITÄT: {0}".format(priority))
        
        print("\n" + "="*80)
    
    def create_kml(self, hotspots: List[Dict], output_path: str) -> bool:
        """KML erstellen."""
        try:
            kml = ET.Element('kml', xmlns='http://www.opengis.net/kml/2.2')
            document = ET.SubElement(kml, 'Document')
            
            name = ET.SubElement(document, 'name')
            name.text = "ULTIMATE Fundstellen + NIR"
            
            for style_id, color, scale in [
                ('ultra_high', 'ffff0000', '1.7'),
                ('very_high', 'ff0000ff', '1.5'),
                ('high', 'ff00a5ff', '1.3')
            ]:
                style = ET.SubElement(document, 'Style', id=style_id)
                icon_style = ET.SubElement(style, 'IconStyle')
                color_elem = ET.SubElement(icon_style, 'color')
                color_elem.text = color
                scale_elem = ET.SubElement(icon_style, 'scale')
                scale_elem.text = scale
            
            for i, hotspot in enumerate(hotspots, 1):
                placemark = ET.SubElement(document, 'Placemark')
                
                pm_name = ET.SubElement(placemark, 'name')
                pm_name.text = "Fundstelle #{0}".format(i)
                
                if 'characteristics' in hotspot:
                    chars = hotspot['characteristics']
                    nir_info = ""
                    if chars.get('has_nir_data', False):
                        # *** FIX: ALLE NIR-Indizes in KML! ***
                        nir_info = f"""
                        <li>🌈 <b>NIR NDVI:</b> {chars['ndvi']:.3f} (Vegetation)</li>
                        <li>🌈 <b>NIR EVI:</b> {chars.get('evi', 0):.3f} (Enhanced Veg)</li>
                        <li>🌈 <b>NIR SAVI:</b> {chars.get('savi', 0):.3f} (Boden-adjustiert) ***</li>
                        <li>🌈 <b>NIR NDWI:</b> {chars.get('ndwi', 0):.3f} (Wasser-Features)</li>
                        """
                    
                    desc = ET.SubElement(placemark, 'description')
                    desc.text = f"""
                    <![CDATA[
                    <h3>ULTIMATE #{i}</h3>
                    <b>Konfidenz:</b> {hotspot['confidence']:.1%}<br/>
                    <b>Coords:</b> {hotspot['lat']:.6f}, {hotspot['lon']:.6f}<br/>
                    <ul>
                    <li><b>LIDAR Relief:</b> {chars['lidar_relief']:.1%}</li>
                    <li><b>Crop Marks:</b> {chars['crop_marks']:.1%}</li>
                    {nir_info}
                    <li><b>3-fach:</b> {"JA" if chars['triple_confirmation'] else "NEIN"}</li>
                    </ul>
                    <p>{hotspot['explanation'].replace(' * ', '<br/>* ')}</p>
                    ]]>
                    """
                
                style_url = ET.SubElement(placemark, 'styleUrl')
                if hotspot['confidence'] > 0.8:
                    style_url.text = '#ultra_high'
                elif hotspot['confidence'] > 0.7:
                    style_url.text = '#very_high'
                else:
                    style_url.text = '#high'
                
                point = ET.SubElement(placemark, 'Point')
                coords = ET.SubElement(point, 'coordinates')
                coords.text = "{0},{1},0".format(hotspot['lon'], hotspot['lat'])
            
            xml_str = minidom.parseString(ET.tostring(kml)).toprettyxml(indent='  ')
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(xml_str)
            
            self.logger.info(f"[KML] Erstellt: {output_path}")
            return True
        except Exception as e:
            self.logger.error(f"Fehler beim Erstellen der KML: {e}")
            return False
    
    def process(self, lidar_png: str, lidar_pgw: str,
                historical_png: Optional[str] = None, 
                historical_pgw: Optional[str] = None,
                aerial_png: Optional[str] = None, 
                aerial_pgw: Optional[str] = None,
                use_gee: bool = False,
                gee_time_range: Optional[Tuple[str, str]] = None,
                output_kml: str = "fundstellen_ultimate.kml", 
                output_heatmap: Optional[str] = None,
                debug_nir: bool = False) -> Optional[List[Dict]]:
        """
        Hauptprozess mit optionaler Google Earth Engine API.
        
        Args:
            use_gee: Wenn True, versuche NIR-Daten von Google Earth Engine zu laden
            gee_time_range: (start_date, end_date) für Sentinel-2 Download
            debug_nir: Wenn True, erstelle Debug-Ausgaben für NIR-Indizes
        """
        print("\n" + "="*80)
        print("*** ULTIMATE TREASURE FINDER - NIR EDITION ***")
        print("="*80 + "\n")
        
        # LIDAR laden
        self.logger.info("[LOAD] Lade LIDAR...")
        lidar_img = self.load_image(lidar_png, as_rgb=True)
        if lidar_img is None:
            self.logger.error("ABBRUCH: LIDAR-Bild konnte nicht geladen werden")
            return None
        
        lidar_world = self.parse_world_file(lidar_pgw)
        if lidar_world is None:
            self.logger.error("ABBRUCH: LIDAR-Georeferenzierung fehlt")
            return None
        
        # Google Earth Engine NIR-Download?
        nir_data = None
        if use_gee and self.gee_connector and self.gee_connector.initialized:
            self.logger.info("🌈 [GEE] Versuche NIR-Daten zu laden...")
            
            # DEBUG: Zeige LIDAR Shape
            self.logger.info(f"   [DEBUG] LIDAR Shape: {lidar_img.shape}")
            self.logger.info(f"   [DEBUG] World-File: {lidar_world}")
            
            # Berechne BBox aus LIDAR
            bbox = self.gee_connector.calculate_bbox_from_pgw(lidar_world, lidar_img.shape)
            self.logger.info(f"   [DEBUG] Berechnete BBox (Original): {bbox}")
            
            # *** FIX: Erweitere BBox um 500m für mehr Kontext! ***
            # Das gibt bessere Crop Mark Detection und höhere Auflösung!
            bbox_buffer_m = 500  # 500 Meter Buffer in alle Richtungen
            bbox_expanded = expand_bbox_wgs84_m(bbox, bbox_buffer_m)
            self.logger.info(f"   [DEBUG] Erweiterte BBox (+{bbox_buffer_m}m): {bbox_expanded}")
            
            # *** Multi-Temporal Mode aus Parametern ***
            multi_temporal_mode = getattr(self, 'multi_temporal_mode', 'ultimate')
            self.logger.info(f"   [MODE] Multi-Temporal: {multi_temporal_mode}")
            
            nir_data = self.gee_connector.download_nir_data(
                bbox=bbox_expanded,  # ← Verwende erweiterte BBox!
                resolution=10,
                time_range=gee_time_range,
                multi_temporal=multi_temporal_mode  # ← Multi-Temporal Mode!
            )
            
            if nir_data:
                mode = nir_data.get('mode', 'unknown')
                years = nir_data.get('years_analyzed', 1)
                self.logger.info(f"✅ Sentinel-2 NIR-Daten erfolgreich geladen! (Mode: {mode}, Jahre: {years})")
            else:
                self.logger.warning("⚠️ Sentinel-2 Download fehlgeschlagen - Fallback zu RGB")
        
        # Historische Karte laden
        hist_img = None
        hist_world = None
        hist_details = None
        if historical_png and historical_pgw:
            self.logger.info("[LOAD] Lade Historische Karte...")
            hist_img = self.load_image(historical_png, as_rgb=True)
            if hist_img is not None:
                hist_world = self.parse_world_file(historical_pgw)
                if hist_world is None:
                    self.logger.warning("Historische Georeferenzierung fehlt")
                    hist_img = None
        else:
            self.logger.info("[SKIP] Historische Karte nicht bereitgestellt")
        
        # Luftbild laden (fallback wenn kein GEE)
        aerial_img = None
        if not nir_data and aerial_png and aerial_pgw:
            self.logger.info("[LOAD] Lade Luftbild (RGB)...")
            aerial_img = self.load_image(aerial_png, as_rgb=True)
            if aerial_img is not None:
                aerial_world = self.parse_world_file(aerial_pgw)
                if aerial_world is None:
                    self.logger.warning("Luftbild-Georeferenzierung fehlt")
                    aerial_img = None
        
        # Bildgrößen-Validierung
        images = {'LIDAR': lidar_img}
        if hist_img is not None:
            images['Historie'] = hist_img
        if aerial_img is not None:
            images['Luftbild'] = aerial_img
        
        self.validate_image_sizes(images)
        
        # Analysen
        print("\n[LIDAR] *** LIDAR-ANALYSE ***")
        lidar_anomaly, lidar_details = self.advanced_lidar_analysis(lidar_img, world_params=lidar_world)
        del lidar_img
        
        hist_features = None
        if hist_img is not None:
            print("\n[HIST] *** HISTORISCHE ANALYSE ***")
            hist_features, hist_details = self.advanced_historical_analysis(hist_img, world_params=lidar_world)
            del hist_img
        
        aerial_features = None
        aerial_details = None
        aerial_rgb_for_mask = None  # *** FIX: Speichere RGB für Urban-Maske ***
        
        if nir_data:
            # NIR-MODE!
            print("\n[AERIAL] *** 🌈 NIR LUFTBILDANALYSE (SENTINEL-2) ***")
            aerial_features, aerial_details = self.advanced_aerial_analysis(
                aerial_rgb=None,
                nir_data=nir_data
            )
            aerial_rgb_for_mask = nir_data['rgb']  # Speichere für Urban-Maske
            
            # *** DEBUG: NIR-Indizes visualisieren (nur wenn --debug-nir) ***
            if debug_nir and aerial_details and aerial_details.get('has_nir', False):
                debug_dir = os.path.dirname(output_kml) if output_kml else "."
                
                # *** FIX: Verwende LIDAR World-Parameter und LIDAR-Shape für korrekte Ausrichtung! ***
                # NIR-Daten werden zu LIDAR-Dimensionen resized, damit die Debug-Dateien deckungsgleich sind
                debug_files = self.save_nir_debug_outputs(
                    aerial_details, 
                    lidar_world,  # *** GEÄNDERT: LIDAR statt NIR für korrekte Georeferenzierung! ***
                    output_dir=debug_dir,
                    lidar_shape=lidar_anomaly.shape  # *** NEU: LIDAR-Dimensionen für Resize ***
                )
                if debug_files:
                    print("\n🔍 [DEBUG] NIR Debug-Dateien erstellt:")
                    for key, path in debug_files.items():
                        print(f"  ✅ {key}: {path}")
        elif aerial_img is not None:
            # RGB-MODE
            print("\n[AERIAL] *** LUFTBILDANALYSE (RGB) ***")
            aerial_rgb_for_mask = aerial_img.copy()  # *** FIX: Kopiere VORHER! ***
            aerial_features, aerial_details = self.advanced_aerial_analysis(
                aerial_rgb=aerial_img,
                nir_data=None
            )
            del aerial_img
        
        print("\n[FUSION] *** ULTIMATE FUSION ***")
        ultimate_correlation = self.ultimate_fusion(
            lidar_anomaly, hist_features, aerial_features,
            lidar_details, hist_details, aerial_details
        )
        
        # Urban-Filter anwenden (NEUE Funktion!)
        if aerial_rgb_for_mask is not None:  # *** FIX: Verwende gespeicherte Variable ***
            print("\n[URBAN] *** URBAN-FILTER ***")
            nir_for_mask = nir_data['nir'] if nir_data else None
            
            urban_mask = self.create_urban_mask(aerial_rgb_for_mask, nir_for_mask)
            
            # Resize urban_mask auf correlation-Größe!
            if urban_mask.shape != ultimate_correlation.shape:
                self.logger.info(f"  [URBAN] Resize Maske: {urban_mask.shape} → {ultimate_correlation.shape}")
                urban_mask = cv2.resize(urban_mask, 
                                       (ultimate_correlation.shape[1], ultimate_correlation.shape[0]),
                                       interpolation=cv2.INTER_NEAREST)
            
            # Reduziere Korrelation in urbanen Bereichen (80% Reduktion)
            self.logger.info("  [URBAN] Wende Maske auf Korrelations-Map an...")
            ultimate_correlation = ultimate_correlation * (1.0 - 0.8 * urban_mask)
        
        del lidar_anomaly
        if hist_features is not None:
            del hist_features
        if aerial_features is not None:
            del aerial_features
        
        if output_heatmap:
            self.save_heatmap(ultimate_correlation, output_heatmap)
        
        print("\n[CLUSTER] *** OPTIMIERTES CLUSTERING ***")
        hotspots = self.cluster_hotspots_optimized(ultimate_correlation, lidar_world, max_points=5000)
        
        if not hotspots:
            self.logger.warning("Keine Hotspots gefunden!")
            return []
        
        # *** FIX v4.2.1: Detaillierte Logging für Hotspot-Filtering ***
        self.logger.info(f"  [CLUSTER] {len(hotspots)} initiale Hotspots gefunden")
        
        print("\n[ANALYZE] Charakterisierung...")
        valid_hotspots = []  # *** NEU: Nur valide Hotspots ***
        invalid_count = 0
        invalid_reasons = {}
        MAX_REASON_LENGTH = 20  # *** FIX v4.2.1: Named constant für String-Slicing ***
        
        for idx, hotspot in enumerate(hotspots):
            chars = self.analyze_hotspot_characteristics(
                hotspot['pixel_x'], hotspot['pixel_y'],
                lidar_details, hist_details, aerial_details,
                ultimate_correlation,
                world_params=lidar_world  # *** FIX v4.2.0 ***
            )
            
            # *** KRITISCHER FIX: Filtere invalide Hotspots ***
            if chars.get('invalid', False):
                reason = chars.get('reason', 'unknown')
                invalid_count += 1
                # Sammle Statistiken über Ablehnungsgründe
                # *** FIX v4.2.1: Robuste Kategorisierung mit Fallback ***
                try:
                    reason_key = reason.split(' ')[0] if ' ' in reason else reason[:MAX_REASON_LENGTH]
                except AttributeError:
                    # Falls reason nicht String ist
                    reason_key = 'unknown'
                invalid_reasons[reason_key] = invalid_reasons.get(reason_key, 0) + 1
                self.logger.debug(f"   ❌ [FILTER] Hotspot {idx+1}/{len(hotspots)} verworfen: {reason}")
                continue  # Überspringe diesen Hotspot!
            
            hotspot['characteristics'] = chars
            valid_hotspots.append(hotspot)  # *** Nur valide hinzufügen ***
        
        # *** Verwende ab jetzt nur noch valid_hotspots ***
        hotspots = valid_hotspots
        
        # *** FIX v4.2.1: Detaillierte Statistik über Filterung ***
        if invalid_count > 0:
            self.logger.info(f"  [FILTER] {invalid_count} Hotspots als invalid gefiltert:")
            for reason, count in invalid_reasons.items():
                self.logger.info(f"    - {reason}: {count}")
        
        if len(hotspots) == 0:
            self.logger.warning("⚠️ Alle Hotspots wurden als invalid gefiltert!")
            return []
        
        self.logger.info(f"✅ {len(hotspots)} valide Hotspots nach Filtering")
        
        # *** OPTIMIERT: Feature-basierte Confidence-Boosts ***
        # *** FIX: Duplicate call removed - characteristics already computed above ***
        for hotspot in hotspots:
            # Characteristics already available from previous loop
            # Defensive check to ensure characteristics exist
            if 'characteristics' not in hotspot:
                self.logger.warning(f"⚠️ Hotspot without characteristics, skipping boost calculation")
                continue
            
            chars = hotspot['characteristics']
            base_confidence = hotspot['confidence']
            feature_boost = 1.0
            
            # SAVI < 0.25 = STARKER Indikator für vergrabene Strukturen
            if chars.get('savi', 1.0) < 0.25:
                feature_boost *= 1.30  # +30%
                
            # SAVI < 0.20 = SEHR STARKER Indikator
            if chars.get('savi', 1.0) < 0.20:
                feature_boost *= 1.20  # Weitere +20% (total +56%)
            
            # NDWI (Wasserstrukturen): Gestufte Bewertung
            # *** FIX: Wissenschaftlich korrekt → 0.3+ = Wasser, nicht 0.7! ***
            # Alte Wassergräben: 0.3-0.6 (niedrig aber relevant!)
            # Moderne Wasserflächen: > 0.6 (stark)
            ndwi_val = chars.get('ndwi', 0.0)
            if ndwi_val > 0.6:
                feature_boost *= 1.30  # +30% (starke Wasserstruktur)
                self.logger.debug(f"   [NDWI] Starke Wasserstruktur ({ndwi_val:.2f}) → +30%")
            elif ndwi_val > 0.4:
                feature_boost *= 1.20  # +20% (Wassergraben/Kanal)
                self.logger.debug(f"   [NDWI] Wassergraben ({ndwi_val:.2f}) → +20%")
            
            # LIDAR Relief > 0.5 = Deutliche Geländeanomalie
            if chars['lidar_relief'] > 0.5:
                feature_boost *= 1.15  # +15%
            
            # LIDAR Relief > 0.7 = Sehr starke Anomalie
            if chars['lidar_relief'] > 0.7:
                feature_boost *= 1.10  # Weitere +10%
            
            # Geometrische Strukturen in LIDAR
            if chars['has_lidar_structure']:
                feature_boost *= 1.10  # +10%
            
            # Historische Strukturen
            if chars['hist_structures'] > 0.3:
                feature_boost *= 1.15  # +15%
            
            # Hohe Crop Marks (nur wenn NIR, da zuverlässiger)
            if chars.get('has_nir_data', False) and chars['crop_marks'] > 0.6:
                feature_boost *= 1.10  # +10%
            
            # Triple Confirmation = PREMIUM (LIDAR + Luftbild + Karte)
            if chars['triple_confirmation']:
                feature_boost *= 1.50  # +50%!
            
            # ═══════════════════════════════════════════════════════════
            # *** MULTI-TEMPORAL BOOSTS (GAME CHANGER!) ***
            # ═══════════════════════════════════════════════════════════
            
            # Persistenz-Boost: Anomalie über MEHRERE Jahre sichtbar!
            persistence = chars.get('persistence', 0)
            
            if persistence >= 3:
                feature_boost *= 2.0  # +100%! (3 Jahre persistent = SICHER!)
                self.logger.debug(f"   [PERSIST-3] Anomalie 3 Jahre sichtbar → +100%")
            elif persistence >= 2:
                feature_boost *= 1.5  # +50% (2 Jahre persistent)
                self.logger.debug(f"   [PERSIST-2] Anomalie 2 Jahre sichtbar → +50%")
            elif persistence >= 1:
                feature_boost *= 1.2  # +20% (1 Jahr sichtbar)
            
            # High Confidence Mask: Erfüllt ALLE Multi-Temporal Kriterien!
            if chars.get('high_confidence', False):
                feature_boost *= 1.8  # +80%
                self.logger.debug(f"   [HIGH-CONF] Multi-Temporal High Confidence → +80%")
            
            # Seasonal Contrast: Niedriger Kontrast = Struktur behindert Wachstum
            # *** FIX: Nur bei niedrigem NDVI relevant! ***
            # *** WISSENSCHAFTLICH KORREKT: Auch obere NDVI-Grenze prüfen! ***
            # Hoher NDVI (>0.6) + niedriger Contrast = Wald, KEIN Crop Mark!
            contrast = chars.get('seasonal_contrast', 1.0)
            ndvi_value = chars.get('ndvi', 1.0)  # Aktueller NDVI-Wert
            
            # Nur booten wenn NDVI im richtigen Bereich ist (0.2-0.5)
            # Zu niedrig (< 0.2) = Boden/Fels
            # Zu hoch (> 0.5) = gesunde Vegetation oder Wald
            if 0.2 <= ndvi_value < 0.5 and contrast < 0.10:
                feature_boost *= 1.5  # +50% (fast kein Wachstum!)
                self.logger.debug(f"   [CONTRAST] Sehr niedriger Seasonal Contrast ({contrast:.2f}) + NDVI im Target-Bereich → +50%")
            elif 0.2 <= ndvi_value < 0.5 and contrast < 0.15:
                feature_boost *= 1.3  # +30%
                self.logger.debug(f"   [CONTRAST] Niedriger Seasonal Contrast ({contrast:.2f}) + NDVI im Target-Bereich → +30%")
            elif 0.2 <= ndvi_value < 0.5 and contrast < 0.20:
                feature_boost *= 1.15  # +15%
            elif ndvi_value >= 0.6:
                # Hoher NDVI = wahrscheinlich Wald, Contrast-Boost nicht anwenden
                self.logger.debug(f"   [CONTRAST] NDVI zu hoch ({ndvi_value:.2f}), wahrscheinlich Wald - Contrast-Boost übersprungen")
            elif ndvi_value < 0.2:
                # Zu niedriger NDVI = Boden/Fels, nicht archäologisch relevant
                self.logger.debug(f"   [CONTRAST] NDVI zu niedrig ({ndvi_value:.2f}), wahrscheinlich Boden/Fels - Contrast-Boost übersprungen")
            
            # ULTIMATE COMBO: Persistenz + High Confidence + Seasonal Contrast!
            # *** FIX: Auch hier NDVI-Range-Check! ***
            if persistence >= 2 and chars.get('high_confidence', False) and contrast < 0.15 and 0.2 <= ndvi_value < 0.5:
                feature_boost *= 1.3  # Weitere +30% für PERFEKTE Kombination!
                self.logger.info(f"   🌟 ULTIMATE COMBO: Persist≥2 + HighConf + LowContrast + NDVI-Range → +30%")
            
            # ═══════════════════════════════════════════════════════════
            
            # *** FIX #2: Maximum Boost Cap (verhindert extreme Werte) ***
            feature_boost = min(feature_boost, 2.5)  # Max 150% Boost
            
            # *** FIX #3: Base Confidence Threshold ***
            # *** FIX v4.2.1: Reduziert von 0.15 auf 0.10 für mehr Hotspots ***
            # Zu schwache Hotspots werden nicht geboostet
            if base_confidence < 0.10:  # Weniger als 10% Base (war 15%)
                self.logger.debug(f"   [THRESHOLD] Base Confidence zu niedrig ({base_confidence*100:.1f}%), kein Boost")
                boosted_confidence = base_confidence  # Kein Boost!
            else:
                # Normaler Boost
                boosted_confidence = base_confidence * feature_boost
            
            # Final Cap bei 98%
            boosted_confidence = min(boosted_confidence, 0.98)
            
            hotspot['confidence'] = boosted_confidence
            hotspot['base_confidence'] = base_confidence  # Speichere Original für Debugging
            hotspot['feature_boost'] = feature_boost
            
            hotspot['explanation'] = self.generate_ultimate_explanation(chars, boosted_confidence)
        
        # *** OPTIMIERT: Sortiere Hotspots nach geboosteter Confidence neu ***
        hotspots.sort(key=lambda x: x['confidence'], reverse=True)
        
        self.print_top_findings(hotspots, top_n=10)
        self.create_kml(hotspots, output_kml)
        
        print("\n[DONE] *** ULTIMATE ANALYSE ABGESCHLOSSEN ***")
        print("[INFO] {0} Hotspots identifiziert".format(len(hotspots)))
        if nir_data:
            print("🌈 [INFO] Analyse mit echten NIR-Daten durchgeführt!")
        
        return hotspots


def main():
    """Hauptprogramm mit Google Earth Engine Support."""
    parser = argparse.ArgumentParser(
        description='Ultimate Treasure Finder - NIR Edition',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Beispiel-Verwendung:

1. MIT Google Earth Engine API (automatischer NIR-Download):
   python treasure_finder_nir.py --lidar neuLIDAR.png --lidar-world neuLIDAR.pgw \\
     --use-gee --gee-project YOUR_CLIENT_ID --gee-project YOUR_SECRET

2. OHNE API (nur lokale Dateien):
   python treasure_finder_nir.py --lidar neuLIDAR.png --lidar-world neuLIDAR.pgw \\
     --aerial aerial.png --aerial-world aerial.pgw
        """
    )
    
    # PFLICHT
    parser.add_argument('--lidar', required=True, help='LIDAR PNG Bilddatei')
    parser.add_argument('--lidar-world', required=True, help='LIDAR PGW World-Datei')
    
    # OPTIONAL
    parser.add_argument('--historic', help='Historische Karte PNG')
    parser.add_argument('--historic-world', help='Historische PGW World-Datei')
    parser.add_argument('--aerial', help='Luftbild PNG (falls kein GEE)')
    parser.add_argument('--aerial-world', help='Luftbild PGW World-Datei')
    
    # GOOGLE EARTH ENGINE API
    parser.add_argument('--use-gee', action='store_true', 
                       help='Verwende Google Earth Engine API für NIR-Daten')
    parser.add_argument('--gee-project', help='GEE Project ID')
    parser.add_argument('--gee-start', help='Start-Datum (YYYY-MM-DD)')
    parser.add_argument('--gee-end', help='End-Datum (YYYY-MM-DD)')
    parser.add_argument('--multi-temporal', choices=['off', 'seasonal', 'ultimate'], 
                       default='ultimate',
                       help='🚀 Multi-Temporal Mode: off=schnell, seasonal=Crop-Saison, ultimate=3-Jahre-Analyse (DEFAULT: ultimate)')
    
    # OUTPUT
    parser.add_argument('--output-kml', default='fundstellen_ultimate_nir.kml')
    parser.add_argument('--output-heatmap', help='Ausgabe Heatmap PNG')
    parser.add_argument('--debug-nir', action='store_true',
                       help='🔍 Erstelle Debug-Ausgaben für NIR-Indizes (PNG + KML)')
    parser.add_argument('--verbose', '-v', action='store_true')
    
    args = parser.parse_args()
    
    # Validierung
    if args.use_gee:
        if not args.gee_project:
            # Versuche aus Config zu lesen
            config_file = os.path.expanduser("~/.earthengine_project")
            if os.path.exists(config_file):
                with open(config_file, 'r') as f:
                    for line in f:
                        if line.startswith('PROJECT_ID='):
                            args.gee_project = line.strip().split('=')[1]
                            break
        
        if not args.gee_project:
            print("❌ FEHLER: --gee-project erforderlich!")
            print("   Entweder --gee-project angeben")
            print("   Oder setup_gee_interactive.py ausführen")
            sys.exit(1)
        
        if not GEE_AVAILABLE:
            print("❌ FEHLER: earthengine-api nicht installiert!")
            print("   Installation: pip install earthengine-api")
            sys.exit(1)
    
    # GEE Config
    gee_config = None
    if args.use_gee:
        gee_config = {
            'project_id': args.gee_project
        }
    
    # Zeitraum
    gee_time_range = None
    if hasattr(args, 'gee_start') and hasattr(args, 'gee_end'):
        if args.gee_start and args.gee_end:
            gee_time_range = (args.gee_start, args.gee_end)
    
    # Dateiprüfung
    print("[CHECK] Dateiprüfung...")
    files = [("LIDAR PNG", args.lidar), ("LIDAR PGW", args.lidar_world)]
    if args.historic:
        files.append(("Historic PNG", args.historic))
    if args.historic_world:
        files.append(("Historic PGW", args.historic_world))
    if args.aerial:
        files.append(("Aerial PNG", args.aerial))
    if args.aerial_world:
        files.append(("Aerial PGW", args.aerial_world))
    
    all_ok = True
    for name, path in files:
        if path and os.path.exists(path):
            print(f"  [OK] {name}")
        elif path:
            print(f"  [FEHLT] {name}: {path}")
            all_ok = False
    
    if not all_ok and not args.use_gee:
        print("\n[FEHLER] Einige Dateien fehlen!")
        sys.exit(1)
    
    log_level = logging.DEBUG if args.verbose else logging.INFO
    
    print("\n" + "="*60)
    finder = UltimateTreasureFinder(log_level=log_level, gee_config=gee_config)
    
    # Setze Multi-Temporal Mode
    finder.multi_temporal_mode = args.multi_temporal
    
    hotspots = finder.process(
        lidar_png=args.lidar,
        lidar_pgw=args.lidar_world,
        historical_png=args.historic,
        historical_pgw=args.historic_world,
        aerial_png=args.aerial,
        aerial_pgw=args.aerial_world,
        use_gee=args.use_gee,
        gee_time_range=gee_time_range,
        output_kml=args.output_kml,
        output_heatmap=args.output_heatmap,
        debug_nir=args.debug_nir  # *** NEU: Debug-Parameter ***
    )
    
    if hotspots:
        print(f"\n✅ Erfolgreich! {len(hotspots)} Fundstellen in {args.output_kml}")
    else:
        print("\n⚠️ Keine Fundstellen identifiziert")
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) == 1:
        # Legacy Mode
        print("[INFO] Legacy-Modus - verwende --help für neue Features!")
        print("[TIPP] Für NIR-Daten: --use-gee --gee-project XXX --gee-project YYY\n")
        
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        folder = os.path.join(desktop, "Full Scan")
        
        finder = UltimateTreasureFinder()
        
        hotspots = finder.process(
            lidar_png=os.path.join(folder, "neuLIDAR.png"),
            lidar_pgw=os.path.join(folder, "neuLIDAR.pgw"),
            historical_png=os.path.join(folder, "historic.png") if os.path.exists(os.path.join(folder, "historic.png")) else None,
            historical_pgw=os.path.join(folder, "historic.pgw") if os.path.exists(os.path.join(folder, "historic.pgw")) else None,
            aerial_png=os.path.join(folder, "aerial.png") if os.path.exists(os.path.join(folder, "aerial.png")) else None,
            aerial_pgw=os.path.join(folder, "aerial.pgw") if os.path.exists(os.path.join(folder, "aerial.pgw")) else None,
            output_kml=os.path.join(folder, "fundstellen_ultimate_nir.kml"),
            output_heatmap=os.path.join(folder, "heatmap_ultimate_nir.png")
        )
    else:
        main()
