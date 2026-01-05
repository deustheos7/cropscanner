# BENUTZERHANDBUCH - Archäologischer Treasure Finder
## Vollständige Anleitung für Erstbenutzer

---

## 📖 INHALTSVERZEICHNIS

1. [Was ist dieser Code?](#was-ist-dieser-code)
2. [Voraussetzungen](#voraussetzungen)
3. [Installation](#installation)
4. [Verwendung](#verwendung)
5. [Ergebnisse verstehen](#ergebnisse-verstehen)
6. [Alle Funktionen im Detail](#alle-funktionen-im-detail)
7. [Beispiele](#beispiele)
8. [Fehlerbehebung](#fehlerbehebung)

---

## 📌 WAS IST DIESER CODE?

### Zweck
**Automatische Detektion archäologischer Fundstellen** durch Kombination mehrerer Datenquellen:

```
LIDAR-Daten → Geländeanomalien (vergrabene Mauern, Gräben)
    +
Satellitendaten → Vegetationsstress (Crop Marks)
    +
Historische Karten → Bekannte Strukturen
    =
FUNDSTELLEN MIT KONFIDENZ-SCORE
```

### Hauptfunktion
Der Code analysiert LIDAR-Höhendaten, Satellitenbilder und historische Karten, um archäologische Stätten zu identifizieren, die unter der Oberfläche verborgen sind.

### Ausgabe
- **KML-Datei** mit allen Fundstellen (öffnen in Google Earth)
- **Heatmap** (PNG-Bild) zeigt Hotspots
- **Debug-Dateien** (optional) für detaillierte Analyse

---

## 🔧 VORAUSSETZUNGEN

### 1. Python-Installation
```bash
# Python 3.8 oder höher erforderlich
python --version  # Sollte 3.8+ anzeigen
```

### 2. Erforderliche Bibliotheken
```bash
pip install numpy
pip install pillow
pip install opencv-python
pip install scipy
pip install scikit-image
pip install scikit-learn
```

### 3. Optional: Google Earth Engine
Nur für automatischen Satelliten-Download erforderlich:
```bash
pip install earthengine-api
```

### 4. Eingabedaten

**PFLICHT:**
- ✅ LIDAR-Höhenbild (PNG-Format)
- ✅ LIDAR World-File (PGW-Format, Georeferenzierung)

**OPTIONAL:**
- Historische Karte (PNG + PGW)
- Luftbild (PNG + PGW)
- Google Earth Engine Account (für automatischen Satelliten-Download)

---

## 📥 INSTALLATION

### Schritt 1: Code herunterladen
```bash
# Repository klonen
git clone https://github.com/deustheos7/cropscanner.git
cd cropscanner
```

### Schritt 2: Bibliotheken installieren
```bash
# Alle Abhängigkeiten installieren
pip install numpy pillow opencv-python scipy scikit-image scikit-learn

# Optional: Google Earth Engine
pip install earthengine-api
```

### Schritt 3: Google Earth Engine einrichten (optional)
```bash
# Authentifizierung
earthengine authenticate

# Project ID eintragen (in ~/.earthengine_project)
echo "PROJECT_ID=dein-gee-projekt-id" > ~/.earthengine_project
```

---

## 🚀 VERWENDUNG

### MODUS 1: Nur LIDAR (Einfachster Modus)

```bash
python treasure_finder_ultimate_gee_v4_2_1_COMPLETE.py \
  --lidar neuLIDAR.png \
  --lidar-world neuLIDAR.pgw \
  --output-kml fundstellen.kml
```

**Was passiert:**
- Analysiert nur LIDAR-Daten
- Erkennt Geländeanomalien (Hügel, Gräben, Strukturen)
- Erstellt KML mit Fundstellen

---

### MODUS 2: LIDAR + Historische Karte

```bash
python treasure_finder_ultimate_gee_v4_2_1_COMPLETE.py \
  --lidar neuLIDAR.png \
  --lidar-world neuLIDAR.pgw \
  --historic historic.png \
  --historic-world historic.pgw \
  --output-kml fundstellen.kml
```

**Was passiert:**
- Analysiert LIDAR + historische Karten
- Erkennt alte Wege, Strukturen aus historischen Karten
- Korreliert mit LIDAR-Anomalien
- Höhere Genauigkeit durch Kreuzvalidierung

---

### MODUS 3: LIDAR + Luftbild

```bash
python treasure_finder_ultimate_gee_v4_2_1_COMPLETE.py \
  --lidar neuLIDAR.png \
  --lidar-world neuLIDAR.pgw \
  --aerial aerial.png \
  --aerial-world aerial.pgw \
  --output-kml fundstellen.kml
```

**Was passiert:**
- Analysiert LIDAR + Luftbild (RGB)
- Erkennt Crop Marks (Vegetationsanomalien)
- Erkennt Soil Marks (Bodenhelligkeitsunterschiede)
- Korreliert mit LIDAR

---

### MODUS 4: PREMIUM - Automatischer Satelliten-Download (Google Earth Engine)

```bash
python treasure_finder_ultimate_gee_v4_2_1_COMPLETE.py \
  --lidar neuLIDAR.png \
  --lidar-world neuLIDAR.pgw \
  --use-gee \
  --gee-project dein-projekt-id \
  --multi-temporal ultimate \
  --output-kml fundstellen.kml \
  --debug-nir
```

**Was passiert:**
- Lädt automatisch Sentinel-2 Satellitendaten für LIDAR-Bereich
- Analysiert 3 JAHRE Daten (Crop Mark Saison + Soil Mark Saison)
- Berechnet Persistenz-Score (wie oft über 3 Jahre sichtbar)
- Berechnet 4 Vegetationsindizes (NDVI, EVI, SAVI, NDWI)
- Erstellt Debug-Dateien für alle Indizes
- **HÖCHSTE GENAUIGKEIT** durch Multi-Temporal-Analyse

---

### MODUS 5: Vollständig (Alle Daten)

```bash
python treasure_finder_ultimate_gee_v4_2_1_COMPLETE.py \
  --lidar neuLIDAR.png \
  --lidar-world neuLIDAR.pgw \
  --historic historic.png \
  --historic-world historic.pgw \
  --use-gee \
  --gee-project dein-projekt-id \
  --multi-temporal ultimate \
  --output-kml fundstellen.kml \
  --output-heatmap heatmap.png \
  --debug-nir \
  --verbose
```

**Was passiert:**
- **ALLE DATENQUELLEN** kombiniert
- 3 Jahre Satellitendaten
- LIDAR-Analyse (6 Features)
- Historische Karten
- Erstellt umfassende Debug-Ausgaben
- **MAXIMALE GENAUIGKEIT**

---

## 📊 ERGEBNISSE VERSTEHEN

### 1. KML-Datei (Hauptausgabe)

**Öffnen in Google Earth:**
1. Google Earth starten
2. Datei → Öffnen → `fundstellen.kml` auswählen
3. Fundstellen werden als Markierungen angezeigt

**Was zeigt die KML:**

```xml
<Placemark>
  <name>Fundstelle #1 (89.5%)</name>  ← Konfidenz-Score
  <description>
    🎯 Confidence: 89.5%
    📍 Koordinaten: 47.123456, 8.654321
    
    === LIDAR FEATURES ===
    Relief: 0.78         ← Geländeanomalie (0-1)
    Gradient: 0.65       ← Neigungsänderung
    
    === CROP MARKS ===
    NDVI: 0.32          ← Vegetation niedrig
    SAVI: 0.18          ← Bodenstress (< 0.25 = gut!)
    
    === MULTI-TEMPORAL ===
    Persistenz: 3 Jahre  ← 3 Jahre sichtbar = SEHR SICHER
    Seasonal Contrast: 0.08  ← Niedriger Kontrast = Struktur
    
    === ERKLÄRUNG ===
    Starke LIDAR-Anomalie (Relief=0.78)
    Niedrige SAVI (0.18) zeigt Wachstumshemmung
    3-Jahres-Persistenz: +100% Boost!
    Triple Confirmation: LIDAR + NIR + Historic
  </description>
</Placemark>
```

**Konfidenz-Score interpretieren:**

| Score | Bedeutung | Empfehlung |
|-------|-----------|------------|
| **90-100%** | Extrem wahrscheinlich | Sofort untersuchen! |
| **75-89%** | Sehr wahrscheinlich | Hohe Priorität |
| **60-74%** | Wahrscheinlich | Mittlere Priorität |
| **50-59%** | Möglich | Weitere Analyse nötig |
| **< 50%** | Unsicher | Niedriger Priorität |

---

### 2. Heatmap (PNG-Bild)

**Datei:** `heatmap.png`

**Farbcodierung:**
- 🔴 **Rot/Gelb** = Hohe Anomalie (Hot Spots)
- 🟡 **Grün** = Mittlere Anomalie
- 🔵 **Blau** = Niedrige Anomalie
- ⚫ **Schwarz** = Keine Anomalie

**Verwendung:**
- Überblick über alle Hotspots
- Muster erkennen (Linien, Cluster)
- Vergleich mit anderen Daten

---

### 3. Debug-Ausgaben (mit --debug-nir)

**Erstellt folgende Dateien:**

#### A) NIR-Index-KMLs
- `debug_ndvi.kml` - NDVI-Werte (Vegetationsdichte)
- `debug_evi.kml` - EVI-Werte (Enhanced Vegetation)
- `debug_savi.kml` - SAVI-Werte (Bodenstress) ⭐ **WICHTIGSTER**
- `debug_ndwi.kml` - NDWI-Werte (Wasserstrukturen)

**Farbcodierung in Index-KMLs:**
- 🔴 **Rot** = Hoher Wert (gesunde Vegetation bei NDVI/EVI)
- 🟡 **Gelb** = Mittlerer Wert
- 🟢 **Grün** = Niedriger Wert
- 🔵 **Blau** = Sehr niedriger Wert (interessant für SAVI!)

**SAVI < 0.25 = PREMIUM für Archäologie!**

#### B) NIR-Debug-PNGs
- `debug_ndvi.png` - NDVI-Visualisierung
- `debug_evi.png` - EVI-Visualisierung
- `debug_savi.png` - SAVI-Visualisierung
- `debug_ndwi.png` - NDWI-Visualisierung
- `debug_nir_rgb.png` - Alle Indizes kombiniert

#### C) Multi-Temporal-Debug
- `debug_persistence.png` - Persistenz über 3 Jahre (0-3)
- `debug_seasonal_contrast.png` - Sommer vs. Frühjahr
- `debug_high_confidence.png` - Hochsichere Bereiche

#### D) Statistik-Datei
- `debug_nir_statistics.txt` - Wertebereiche, Perzentile

**Beispiel-Statistik:**
```
=== NDVI STATISTICS ===
Min: -0.12
Max: 0.89
Mean: 0.45
Median: 0.48
Std: 0.18
- < 0.3: 15.2% (schwache Vegetation)
- 0.3-0.7: 68.5% (mittlere Vegetation)
- > 0.7: 16.3% (dichte Vegetation)

=== SAVI STATISTICS ===
- < 0.20: 12.4% (SEHR INTERESSANT für Archäologie!)
- < 0.25: 23.1% (PREMIUM-Bereich)
```

---

## 🔍 ALLE FUNKTIONEN IM DETAIL

### 1. LIDAR-ANALYSE (6 Features)

#### Relief (Gewicht: 40%)
- **Was:** Lokale Höhenunterschiede mit Z-Score-Normalisierung
- **Erkennt:** Hügel, Gräben, vergrabene Mauern
- **Wert:** 0 = flach, 1 = starke Anomalie

#### Gradient (Gewicht: 15%)
- **Was:** Neigungsänderungen (Sobel-Operator)
- **Erkennt:** Strukturgrenzen, Terrassen
- **Wert:** 0 = gleichmäßig, 1 = starke Neigung

#### Edges (Gewicht: 25%)
- **Was:** Kanten-Detektion (Canny)
- **Erkennt:** Scharfe Strukturgrenzen
- **Wert:** 0/1 (binär)

#### Morphologie (Gewicht: 15%)
- **Was:** Top-Hat (Hügel) + Black-Hat (Gräben)
- **Erkennt:** Erhebungen und Vertiefungen
- **Wert:** 0-1

#### Multiscale (Gewicht: 5%)
- **Was:** Analyse auf 3 Skalen (5m, 10m, 15m)
- **Erkennt:** Features verschiedener Größen
- **Wert:** 0-1

#### Adaptive Threshold
- **Was:** Datengesteuerte Schwellwerte
- **3 Modi:**
  - Stark variierendes Gelände: Mean + 1.5σ
  - Mittleres Gelände: 90. Perzentil
  - Flaches Gelände: 85. Perzentil

---

### 2. VEGETATIONSINDIZES (NIR-Daten)

#### NDVI (Normalized Difference Vegetation Index)
```
NDVI = (NIR - RED) / (NIR + RED)
```
- **Range:** -1 bis +1
- **Bedeutung:**
  - < 0.2: Boden, Fels, Wasser
  - 0.2-0.5: Schwache Vegetation (**archäologisch interessant!**)
  - 0.5-0.7: Mittlere Vegetation
  - > 0.7: Dichte Vegetation (Wald)

#### EVI (Enhanced Vegetation Index)
```
EVI = 2.5 * (NIR - RED) / (NIR + 6*RED - 7.5*BLUE + 1)
```
- **Range:** -1 bis +1
- **Vorteil:** Besser bei hoher Biomasse
- **Verwendung:** Zusätzliche Validierung

#### SAVI (Soil-Adjusted Vegetation Index) ⭐ **SCHLÜSSEL-INDEX**
```
SAVI = ((NIR - RED) / (NIR + RED + 0.5)) * 1.5
```
- **Range:** 0 bis ~0.5
- **Vorteil:** Minimiert Bodenhelligkeitseffekte
- **PREMIUM für Archäologie:**
  - < 0.20: SEHR STARKER Indikator
  - < 0.25: PREMIUM-Bereich
  - 0.25-0.35: Interessant
  - > 0.35: Normal

#### NDWI (Normalized Difference Water Index)
```
NDWI = (GREEN - NIR) / (GREEN + NIR)
```
- **Range:** -1 bis +1
- **Bedeutung:**
  - > 0.7: Permanentes Wasser (wird gefiltert)
  - 0.4-0.7: Wasserstrukturen (alte Gräben, Kanäle)
  - < 0.4: Kein Wasser

---

### 3. CROP MARKS DETEKTION

**Methode:** Varianz-Analyse

```python
# NIR-basiert (Premium)
NDVI-Varianz = abs(NDVI - blur(NDVI, 15x15))
SAVI-Varianz = abs(SAVI - blur(SAVI, 15x15))
Crop Marks = 0.35 * NDVI-Varianz + 0.65 * SAVI-Varianz
```

**Warum Varianz?**
- Normale Felder: Gleichmäßige Vegetation
- Vergrabene Strukturen: Lokale Vegetationsunterschiede
- Varianz erkennt diese Unterschiede

**Warum SAVI 65%?**
- SAVI ist zuverlässiger für archäologische Kontexte
- Minimiert Einfluss von Bodenhelligkeit
- Geht über Standard-NDVI-Methoden hinaus

---

### 4. MULTI-TEMPORAL-ANALYSE (3 Jahre)

#### Persistenz-Score (0-3)
- **Berechnung:** Wie oft über 3 Jahre sichtbar
- **Werte:**
  - 0: Nie sichtbar
  - 1: 1 Jahr sichtbar
  - 2: 2 Jahre sichtbar (+50% Boost)
  - 3: Alle 3 Jahre sichtbar (+100% Boost) ⭐

**Warum wichtig?**
- Landwirtschaftliche Anomalien sind temporär
- Archäologische Strukturen sind persistent
- 3-Jahres-Persistenz = SEHR HOHE SICHERHEIT

#### Seasonal Contrast
- **Berechnung:** Sommer-NDVI - Frühjahr-NDVI
- **Niedriger Kontrast (<0.15):** Struktur behindert Wachstum
- **Hoher Kontrast (>0.3):** Normale Vegetation

#### High Confidence Mask
**Kriterien (ALLE müssen erfüllt sein):**
1. Persistenz ≥ 2 Jahre
2. Seasonal Contrast < 0.2
3. NDVI < 0.4

**Bei Erfüllung:** +80% Boost!

---

### 5. FUSION-SYSTEM

#### Base Fusion (Adaptive Gewichtung)

**MIT NIR (Premium):**
```
Fusion = 50% NIR + 25% LIDAR + 25% Historic
```

**OHNE NIR:**
```
Fusion = 40% LIDAR + 30% Aerial + 30% Historic
```

#### Bonus-System (4 Typen)

**1. Crop-LIDAR Bonus (20%)**
```
Crop Marks × LIDAR Relief
```
- Kombiniert Vegetationsstress mit Geländeanomalie

**2. SAVI-LIDAR Bonus (30%)** ⭐ **HÖCHSTER**
```
(1 - SAVI) × LIDAR Relief
```
- Niedrige SAVI + hoher Relief = archäologisch interessant
- SAVI wird invertiert (niedrig = gut)

**3. NDWI-LIDAR Bonus (20%)**
```
(NDWI > 0.4) × LIDAR Relief
```
- Erkennt alte Wassergräben, Kanäle

**4. Triple Pattern Bonus (25%)**
```
LIDAR-Struktur × Aerial-Struktur × Historic-Struktur
```
- 3-fach Bestätigung = sehr sicher

---

### 6. BOOSTING-SYSTEM (6 Typen)

#### 1. LIDAR Relief Boost
- Relief > 0.5: +15%
- Relief > 0.7: Weitere +10%

#### 2. LIDAR Struktur Boost
- Geometrische Struktur erkannt: +10%

#### 3. Historische Struktur Boost
- Historische Struktur > 0.3: +15%

#### 4. Crop Marks Boost (nur mit NIR)
- Crop Marks > 0.6: +10%

#### 5. Triple Confirmation Boost
- Alle 3 Quellen bestätigen: +50%!

#### 6. Multi-Temporal Boosts
- Persistenz 3 Jahre: +100%
- Persistenz 2 Jahre: +50%
- Persistenz 1 Jahr: +20%
- High Confidence: +80%
- Seasonal Contrast (mit NDVI-Range!):
  - NDVI 0.2-0.5 + Contrast < 0.10: +50%
  - NDVI 0.2-0.5 + Contrast < 0.15: +30%
  - NDVI ≥ 0.6: Kein Boost (Wald!)
  - NDVI < 0.2: Kein Boost (Boden!)

---

### 7. FALSE POSITIVE FILTERING (3 Filter)

#### Filter 1: Wasser/Schnee
```
if NDVI < -0.2:
    → Verwerfen (Wasser/Schnee/Wolken)
```

#### Filter 2: Dichter Wald
```
if NDVI > 0.85 AND Relief < 0.3:
    → Verwerfen (Dichter Wald ohne LIDAR-Anomalie)
```

#### Filter 3: Permanentes Wasser
```
if NDWI > 0.7:
    → Verwerfen (See, Fluss)
```

---

### 8. URBAN FILTERING (3 Methoden)

#### Methode 1: NDVI-basiert
- NDVI < 0.2 = Asphalt/Beton

#### Methode 2: Grau-Detektion
- Niedrige Saturation = Straßen/Gebäude

#### Methode 3: Kantendichte
- Viele Kanten = Gebäude

**Schwellwert:** Urban Score > 0.5 = ausschließen

---

### 9. CLUSTERING (DBSCAN)

**Parameter:**
- **Epsilon:** 20m (archäologische Cluster-Distanz)
- **Min Samples:** 3
- **Resolution-aware:** Epsilon passt sich Auflösung an

**Adaptive Thresholds:**
- Viele starke Anomalien: 70%
- Moderate Anomalien: 60%
- Wenige schwache Anomalien: 50%

---

## 💡 BEISPIELE

### Beispiel 1: Einfache Analyse (nur LIDAR)

```bash
python treasure_finder_ultimate_gee_v4_2_1_COMPLETE.py \
  --lidar /pfad/zu/lidar.png \
  --lidar-world /pfad/zu/lidar.pgw \
  --output-kml ergebnis.kml
```

**Erwartete Ausgabe:**
```
[CHECK] Dateiprüfung...
  [OK] LIDAR PNG
  [OK] LIDAR PGW

[INFO] Lade LIDAR...
  [LIDAR-RELIEF] Relief mit lokalem Z-Score...
  [LIDAR-MORPH] Morphologische Features...
  [LIDAR-MULTI] Multiskalen...
  [THRESHOLD] Adaptive Schwellwertfindung...

[FUSION] 1-Quellen-Modus (nur LIDAR)

[CLUSTERING] Identifiziere Hotspots...
  Threshold: 0.60
  DBSCAN: eps=15px, min_samples=3
  → 42 Hotspots gefunden

[TOP 10 FUNDSTELLEN]
1. (89.5%) @ 47.123, 8.456 - Starke LIDAR-Anomalie
2. (85.2%) @ 47.125, 8.458 - Relief=0.78
...

[DONE] *** ANALYSE ABGESCHLOSSEN ***
[INFO] 42 Hotspots identifiziert
```

---

### Beispiel 2: Premium-Analyse (3 Jahre Satelliten)

```bash
python treasure_finder_ultimate_gee_v4_2_1_COMPLETE.py \
  --lidar /pfad/zu/lidar.png \
  --lidar-world /pfad/zu/lidar.pgw \
  --use-gee \
  --gee-project mein-projekt-123 \
  --multi-temporal ultimate \
  --output-kml fundstellen_premium.kml \
  --debug-nir \
  --verbose
```

**Erwartete Ausgabe:**
```
✅ Google Earth Engine initialisiert (Project: mein-projekt-123)

🚀 ULTIMATE MODE: Multi-Temporal Analyse über 3 Jahre...
   BBox: 8.1234,47.5678 .. 8.2345,47.6789
   Auflösung: 10m
   Dies kann 60-120 Sekunden dauern...

   📅 Lade Crop Mark Saisons (Juli-Aug)...
      2025: 12 Szenen → Median-Composite erstellt
      2024: 15 Szenen → Median-Composite erstellt
      2023: 10 Szenen → Median-Composite erstellt

   📅 Lade Soil Mark Saisons (Apr-Mai)...
      2025: 8 Szenen → Median-Composite erstellt
      2024: 11 Szenen → Median-Composite erstellt
      2023: 9 Szenen → Median-Composite erstellt

   🔍 Berechne Persistenz-Score...
      ✅ Persistenz über 3 Jahre berechnet

   📊 Berechne Seasonal Contrast...
      ✅ Contrast berechnet (Crop - Soil)

   🎯 Berechne High Confidence Mask...
      ✅ High Confidence: Persistenz≥2 AND Contrast<0.2 AND NDVI<0.4

   📥 Downloade Multi-Temporal Daten...
   ✅ Download abgeschlossen (45.2 MB)

✅ ULTIMATE Multi-Temporal Daten erfolgreich geladen!
   📊 High Confidence Pixel: 8.5%
   📊 Mittlere Persistenz: 1.8

  🌈 [NIR MODE] Verwende echte Satellitendaten!
  [NIR] Berechne Vegetation Indices...
  NDVI Bereich: -0.15 bis 0.89
  EVI Bereich: -0.12 bis 0.78
  SAVI Bereich: 0.02 bis 0.48
  NDWI Bereich: -0.45 bis 0.65

[FUSION] 🌈 PREMIUM NIR-MODUS (50% NIR-Gewicht)!
  [BONUS] 🌈 NIR Crop Mark Bonus: +20%
  [BONUS] 🌈 SAVI-LIDAR Bonus: +30%
  [BONUS] 🌈 NDWI-LIDAR Bonus: +20%

   🌟 ULTIMATE COMBO: Persist≥2 + HighConf + LowContrast + NDVI-Range → +30%

[TOP 10 FUNDSTELLEN]
1. (96.8%) @ 47.123, 8.456
   - 3-Jahres-Persistenz: +100% Boost!
   - SAVI=0.18 (< 0.25 PREMIUM!)
   - Seasonal Contrast=0.08
   - Triple Confirmation!

[DONE] *** ULTIMATE ANALYSE ABGESCHLOSSEN ***
[INFO] 67 Hotspots identifiziert
🌈 [INFO] Analyse mit echten NIR-Daten durchgeführt!

🔍 [DEBUG] NIR Debug-Dateien erstellt:
  - debug_ndvi.kml
  - debug_savi.kml
  - debug_persistence.png
  ...
```

---

## 🛠️ FEHLERBEHEBUNG

### Problem 1: "earthengine-api nicht installiert"

**Lösung:**
```bash
pip install earthengine-api
earthengine authenticate
```

---

### Problem 2: "GEE Initialisierung fehlgeschlagen"

**Mögliche Ursachen:**
1. Keine Authentifizierung
2. Falsche Project ID
3. Keine Internet-Verbindung

**Lösung:**
```bash
# Neu authentifizieren
earthengine authenticate

# Project ID prüfen
cat ~/.earthengine_project

# Oder direkt angeben
python script.py --gee-project deine-projekt-id
```

---

### Problem 3: "World-File Fehler"

**Symptom:**
```
Fehler beim Parsen von World-File
```

**Lösung:**
World-File (.pgw) muss 6 Zeilen haben:
```
0.000050000000000  ← Pixel-Größe X
0.000000000000000  ← Rotation Y
0.000000000000000  ← Rotation X
-0.000050000000000 ← Pixel-Größe Y (negativ!)
8.123456789000000  ← Upper Left X (Longitude)
47.987654321000000 ← Upper Left Y (Latitude)
```

---

### Problem 4: "Zu wenig Crop Mark Daten"

**Symptom:**
```
⚠️ Zu wenig Crop Mark Daten, fallback zu seasonal...
```

**Ursache:**
- Zu viele Wolken in 3-Jahres-Zeitraum
- Kein Sentinel-2 Coverage

**Lösung:**
- Verwende `--multi-temporal seasonal` statt `ultimate`
- Oder `--multi-temporal off` für einzelnes Datum

---

### Problem 5: "Keine Hotspots gefunden"

**Mögliche Ursachen:**
1. Schwellwert zu hoch
2. Daten zu homogen
3. Falsches Gebiet

**Lösung:**
- Prüfe Heatmap-PNG: Gibt es überhaupt Anomalien?
- Verwende `--verbose` für Details
- Prüfe LIDAR-Qualität
- Versuche andere Parameter

---

### Problem 6: "Zu viele False Positives"

**Symptom:**
Viele Fundstellen in Städten, Wäldern

**Lösung:**
- Urban-Filtering ist bereits aktiv
- False-Positive-Filter sind implementiert
- Prüfe Confidence-Scores: Nur >75% ernst nehmen
- Verwende Multi-Temporal-Modus für höhere Genauigkeit

---

## 📋 KOMMANDOZEILEN-PARAMETER

### Pflicht-Parameter
```bash
--lidar PATH             # LIDAR PNG-Datei
--lidar-world PATH       # LIDAR PGW World-File
```

### Optional-Parameter
```bash
--historic PATH          # Historische Karte PNG
--historic-world PATH    # Historische PGW
--aerial PATH            # Luftbild PNG
--aerial-world PATH      # Luftbild PGW
```

### Google Earth Engine
```bash
--use-gee                # GEE aktivieren
--gee-project ID         # GEE Project ID
--gee-start YYYY-MM-DD   # Start-Datum
--gee-end YYYY-MM-DD     # End-Datum
--multi-temporal MODE    # off/seasonal/ultimate (Default: ultimate)
```

### Ausgabe
```bash
--output-kml PATH        # KML-Ausgabe (Default: fundstellen_ultimate_nir.kml)
--output-heatmap PATH    # Heatmap PNG
--debug-nir              # NIR Debug-Dateien erstellen
--verbose / -v           # Ausführliche Logs
```

---

## 🎓 WISSENSCHAFTLICHE GRUNDLAGEN

### Literatur-Referenzen

1. **Tucker (1979)** - "Red and photographic infrared linear combinations for monitoring vegetation"
   - NDVI-Grundlage

2. **Huete (1988)** - "A soil-adjusted vegetation index (SAVI)"
   - **SCHLÜSSEL-REFERENZ** für diesen Code
   - SAVI minimiert Bodeneffekte

3. **Huete et al. (2002)** - "Overview of MODIS vegetation indices"
   - EVI-Implementierung

4. **McFeeters (1996)** - "NDWI in the delineation of open water features"
   - NDWI für Wasserstrukturen

5. **Woebbecke et al. (1995)** - "Color indices for weed identification"
   - ExG Index für RGB-Modus

---

## 🏆 BESTE PRAKTIKEN

### 1. Datenqualität
- ✅ Verwende hochauflösende LIDAR-Daten (< 1m/Pixel)
- ✅ Prüfe World-File auf Korrektheit
- ✅ Verwende wolkenfreie Satellitenbilder

### 2. Multi-Temporal-Modus
- ✅ `--multi-temporal ultimate` für beste Ergebnisse
- ✅ Mindestens 2 Jahre Daten für Persistenz
- ✅ `--debug-nir` für Validierung

### 3. Ergebnis-Validierung
- ✅ Fokus auf Confidence > 75%
- ✅ Prüfe Persistenz-Score (3 = beste)
- ✅ Vergleiche mit historischen Karten
- ✅ Besuche Top-10 Fundstellen im Feld

### 4. Iterative Verbesserung
- ✅ Erste Analyse: nur LIDAR
- ✅ Zweite Analyse: + Historische Karten
- ✅ Dritte Analyse: + GEE Multi-Temporal
- ✅ Vergleiche Ergebnisse

---

## 📞 SUPPORT

### Bei Fragen oder Problemen:

1. **Dokumentation prüfen:**
   - COMPREHENSIVE_CODE_REVIEW.md (Technische Details)
   - SCIENTIFIC_CODE_REVIEW.md (Wissenschaftliche Validierung)
   - README_COMPLETE_VERIFICATION.md (Englisch)

2. **Logs analysieren:**
   - Verwende `--verbose` für detaillierte Ausgabe
   - Prüfe Fehler-Traceback

3. **Debug-Modus:**
   - Verwende `--debug-nir` für NIR-Debug-Dateien
   - Analysiere Statistik-Datei

---

## ✨ ZUSAMMENFASSUNG

### Was dieser Code macht:
✅ Kombiniert LIDAR, Satelliten und historische Karten  
✅ Berechnet 4 Vegetationsindizes (NDVI, EVI, SAVI, NDWI)  
✅ Analysiert 3 Jahre Satellitendaten (Multi-Temporal)  
✅ Erkennt persistente Anomalien  
✅ Filtert False Positives (Wald, Wasser, Städte)  
✅ Gibt Konfidenz-Score für jede Fundstelle  

### Wie man es benutzt:
1. LIDAR-Daten + World-File vorbereiten
2. Optional: Google Earth Engine Account
3. Script mit passenden Parametern ausführen
4. KML in Google Earth öffnen
5. Top-Fundstellen (>75%) besuchen

### Beste Ergebnisse mit:
- `--multi-temporal ultimate` (3 Jahre)
- `--debug-nir` (Validierung)
- Hochauflösende LIDAR-Daten
- Historische Karten zur Kreuzvalidierung

---

**Viel Erfolg bei der archäologischen Prospektion!** 🏛️
