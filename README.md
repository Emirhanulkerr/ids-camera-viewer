# IDS Camera Viewer (UI-5490SE-M-GL R2)

PyQt5 + pyueye tabanlı, IDS UI-5490SE-M-GL R2 monokrom GigE kamera için canlı görüntüleme uygulaması.

## Özellikler

- Ayrı thread'de kamera grab (UI'i kilitlemez)
- Pixel clock + auto-shutter tuning (max FPS)
- Frame event tabanlı bekleme (CPU yakmaz)
- 30 Hz UI render, 1 Hz FPS sorgulama
- Full-res kayıt, downscaled preview

---

## 1) Geliştirme ortamı kurulumu (Windows)

```bat
py -3 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python camera.py
```

> Mac üzerinde `pyueye` çalışmaz; geliştirmeyi Windows'ta yapın.

---

## 2) EXE Build (Windows)

Proje klasöründe **çift tıkla** veya CMD'den çalıştır:

```bat
build.bat
```

Çıktı: `dist\IDSCamera.exe`

Sorun yaşıyorsanız konsollu sürümle hata mesajını görmek için:

```bat
build_debug.bat
```

Çıktı: `dist\IDSCamera_debug.exe` — CMD'den açın, log mesajları görünür.

---

## 3) EXE'yi BAŞKA bir bilgisayarda çalıştırma

EXE'yi taşıdığınız makinede **iki şey gerekli**:

### a) IDS Software Suite (zorunlu)
`pyueye`, `ueye_api_64.dll` üzerinden kamerayla konuşur. Bu DLL bir sürücüdür ve EXE'nin içine paketlenemez. Hedef makineye kurulmalıdır.

- İndir: <https://en.ids-imaging.com/download-ueye.html>
- Kurulum sırasında **GigE Camera** desteğini seçin.
- Kurulduktan sonra **IDS Camera Manager**'ı açıp kameranızı görebildiğinizi doğrulayın (IP atayın gerekirse).

### b) Visual C++ Redistributable (Win 10/11'de genelde var)

PyQt5 ve OpenCV bunu gerektirir. Win 10/11'de %95 ihtimalle yüklüdür. Yoksa:

- İndir: <https://aka.ms/vs/17/release/vc_redist.x64.exe>

### Dağıtım paketi içeriği

Hedef bilgisayara şunu kopyalamak yeterli:

```
IDSCamera.exe
```

Sadece tek dosya — `--onefile` modunda derlendiği için.

---

## 4) Sık karşılaşılan sorunlar

| Hata mesajı | Neden | Çözüm |
|---|---|---|
| **"IDS Driver Eksik" popup'ı** | IDS Software Suite kurulu değil | (3a)'yı yapın |
| **`is_InitCamera basarisiz (kod=3)`** | Başka uygulama kamerayı kullanıyor | IDS Camera Manager / uEye Cockpit'i kapatın |
| **`is_InitCamera basarisiz (kod=125)`** | Kamera bulunamıyor | Ağ kablosu, IP ayarı, switch / firewall kontrolü. IDS Camera Manager'da kameranın "Available" olduğunu doğrulayın |
| **EXE çift tıklayınca hiçbir şey açılmıyor** | Sessiz kraş | `build_debug.bat` ile derleyip CMD'den çalıştırın, hatayı görün |
| **`Could not find platform plugin "windows"`** | Qt platform plugin eksik | `build.bat`'ı yeniden çalıştırın (PyQt5 collect-submodules zaten ekli) |
| **Antivirus EXE'yi siliyor** | PyInstaller'a karşı yaygın false-positive | Antivirüse istisna ekleyin veya `--onedir` build kullanın |

---

## 5) Performans ipuçları

Kamerayı daha hızlı yapmak için kod içinde otomatik:

- Pixel clock max'a çekiliyor
- Auto-shutter aktif
- GigE packet filter kapatılıyor
- Yüksek öncelikli grab thread

Eğer hala yavaşsa Windows'ta **IDS Camera Manager → Open Camera → "Tools → Pixel Clock"** kısmından manuel olarak max'a kontrol edin. Network kartınızın **Jumbo Frames** desteğini de açın (9000 bytes).

---

## Dosya yapısı

```
camera.py            # Ana uygulama
requirements.txt     # Python bağımlılıkları
build.bat            # Production EXE build (windowed)
build_debug.bat      # Debug EXE build (console)
README.md            # Bu dosya
```
