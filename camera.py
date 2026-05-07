import sys
import time
import logging
import cv2
import numpy as np

from PyQt5.QtWidgets import (
    QApplication,
    QLabel,
    QPushButton,
    QHBoxLayout,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
    QFileDialog,
    QMessageBox,
)

from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtCore import QTimer, Qt, QThread, pyqtSignal, QMutex, QMutexLocker

try:
    from pyueye import ueye
    HAVE_UEYE = True
    UEYE_IMPORT_ERROR = None
except Exception as _e:
    ueye = None
    HAVE_UEYE = False
    UEYE_IMPORT_ERROR = str(_e)


logger = logging.getLogger(__name__)

# UI yenileme araligi: ekrani 30 Hz'de cizmek goze yetiyor; grab thread
# kameranin gercek hizinda calisacak ve son frame'i bize verecek.
UI_REFRESH_MS = 33
MAX_PREVIEW_WIDTH = 1280
# Surucuden FPS okumak da pahali; 1 saniyede bir okuruz.
FPS_QUERY_INTERVAL_S = 1.0


def setup_logging():
    """Debug console icin log formatini ve seviyesini ayarlar."""
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


class IDS_Camera:
    """IDS uEye (pyueye) kamera baglantisini yonetir."""

    def __init__(self):
        """Kamerayi baslatir, goruntu buffer'ini ayarlar ve canli akis baslatir."""
        logger.debug("IDS_Camera init basladi")
        if not HAVE_UEYE:
            raise RuntimeError("pyueye (IDS SDK) not available")

        self.hCam = ueye.HIDS(0)
        self._frame_event_enabled = False

        ret = ueye.is_InitCamera(self.hCam, None)

        if ret != ueye.IS_SUCCESS:
            raise Exception("Kamera başlatılamadı")

        logger.info("IDS kamera baslatildi")

        # Monochrome IDS kamerada MONO8 kullanmak (BGR8 yerine) islem maliyetini dusurur.
        self.channels = 1
        self.bits_per_pixel = 8
        self.is_mono = True

        set_mode_ret = ueye.is_SetColorMode(self.hCam, ueye.IS_CM_MONO8)
        if set_mode_ret != ueye.IS_SUCCESS:
            self.channels = 3
            self.bits_per_pixel = 24
            self.is_mono = False
            ueye.is_SetColorMode(self.hCam, ueye.IS_CM_BGR8_PACKED)
            logger.warning("MONO8 ayarlanamadi, BGR8 moduna gecildi")

        # GigE kameralarda paket boyutunu otomatik max'a cek (drop'lari azaltir).
        try:
            ueye.is_SetPacketFilter(self.hCam, ueye.IS_PACKET_FILTER_OFF)
        except Exception:
            pass

        # 1) Pixel Clock'u maximum'a cek - FPS'in en buyuk belirleyicisi.
        self._set_max_pixel_clock()

        # 2) Hedef FPS'i max iste; surucu uygun olani secer.
        self._request_max_fps()

        # 3) Exposure'i kisa tut. Cok uzunsa FPS'i kapatir; auto-shutter aciyoruz.
        self._enable_auto_shutter()

        self.rectAOI = ueye.IS_RECT()
        ueye.is_AOI(
            self.hCam,
            ueye.IS_AOI_IMAGE_GET_AOI,
            self.rectAOI,
            ueye.sizeof(self.rectAOI),
        )

        self.width = self.rectAOI.s32Width
        self.height = self.rectAOI.s32Height

        self.pcImageMemory = ueye.c_mem_p()
        self.MemID = ueye.int()

        ueye.is_AllocImageMem(
            self.hCam,
            self.width,
            self.height,
            self.bits_per_pixel,
            self.pcImageMemory,
            self.MemID,
        )

        ueye.is_SetImageMem(
            self.hCam,
            self.pcImageMemory,
            self.MemID,
        )

        # 4) Frame event'i ac: grab thread bu olayi bekleyerek
        # tam yeni frame geldiginde uyanir, busy-loop yapmaz.
        try:
            r = ueye.is_EnableEvent(self.hCam, ueye.IS_SET_EVENT_FRAME)
            if r == ueye.IS_SUCCESS:
                self._frame_event_enabled = True
                logger.info("Frame event aktif")
        except Exception:
            logger.debug("Frame event API desteklenmiyor, polling kullanilacak")

        ueye.is_CaptureVideo(self.hCam, ueye.IS_DONT_WAIT)
        logger.debug("IDS canli akis baslatildi")

    def _set_max_pixel_clock(self):
        """Pixel clock'u kameranin destekledigi maksimuma getirir."""
        try:
            pc_range = (ueye.UINT * 3)()
            r = ueye.is_PixelClock(
                self.hCam,
                ueye.IS_PIXELCLOCK_CMD_GET_RANGE,
                pc_range,
                ueye.sizeof(pc_range),
            )
            if r != ueye.IS_SUCCESS:
                logger.debug("Pixel clock range okunamadi")
                return
            pc_min, pc_max, pc_inc = int(pc_range[0]), int(pc_range[1]), int(pc_range[2])
            target = ueye.UINT(pc_max)
            r = ueye.is_PixelClock(
                self.hCam,
                ueye.IS_PIXELCLOCK_CMD_SET,
                target,
                ueye.sizeof(target),
            )
            if r == ueye.IS_SUCCESS:
                logger.info("Pixel clock %s MHz olarak ayarlandi (min=%s max=%s)", pc_max, pc_min, pc_max)
            else:
                logger.warning("Pixel clock ayarlanamadi (kod=%s)", r)
        except Exception as e:
            logger.debug("Pixel clock ayarinda hata: %s", e)

    def _request_max_fps(self):
        """Pixel clock ayarlandiktan sonra surucunun verebilecegi en yuksek FPS'i ister."""
        try:
            requested_fps = ueye.double(1000.0)  # max iste; surucu kirpip donduruyor
            real_fps = ueye.double()
            r = ueye.is_SetFrameRate(self.hCam, requested_fps, real_fps)
            if r == ueye.IS_SUCCESS:
                logger.info("FPS hedefi: %.2f", real_fps.value)
        except Exception:
            logger.debug("is_SetFrameRate desteklenmiyor")

    def _enable_auto_shutter(self):
        """Auto exposure (auto-shutter) aktif edilir."""
        try:
            enable = ueye.double(1)  # 1 = on
            zero = ueye.double(0)
            r = ueye.is_SetAutoParameter(
                self.hCam,
                ueye.IS_SET_ENABLE_AUTO_SHUTTER,
                enable,
                zero,
            )
            if r == ueye.IS_SUCCESS:
                logger.info("Auto shutter aktif")
            else:
                # Manuel olarak makul kisa bir exposure
                exp = ueye.double(8.0)  # ~8 ms
                ueye.is_Exposure(
                    self.hCam,
                    ueye.IS_EXPOSURE_CMD_SET_EXPOSURE,
                    exp,
                    ueye.sizeof(exp),
                )
                logger.info("Manual exposure 8ms olarak ayarlandi")
        except Exception:
            logger.debug("Exposure API desteklenmiyor")

    def wait_for_frame(self, timeout_ms=200):
        """Yeni bir frame'in hazirlanmasini bekler. Frame event aktifse bu cagri
        CPU yakmadan uyur; degilse cagiran tarafin polling yapmasi gerekir.
        Returns True if a new frame is available."""
        if self._frame_event_enabled:
            try:
                r = ueye.is_WaitEvent(
                    self.hCam,
                    ueye.IS_SET_EVENT_FRAME,
                    int(timeout_ms),
                )
                return r == ueye.IS_SUCCESS
            except Exception:
                return True
        return True

    def get_frame(self):
        """Kamera buffer'indaki son kareyi numpy array olarak dondurur."""
        array = ueye.get_data(
            self.pcImageMemory,
            self.width,
            self.height,
            self.bits_per_pixel,
            self.width * self.channels,
            copy=False,
        )

        if self.is_mono:
            frame = np.reshape(
                array,
                (self.height.value, self.width.value),
            )
        else:
            frame = np.reshape(
                array,
                (self.height.value, self.width.value, 3),
            )

        return frame

    def close(self):
        """Canli akis ve ayrilan bellek kaynaklarini guvenli sekilde kapatir."""
        logger.debug("IDS kamera kapatma islemi basladi")
        try:
            if self._frame_event_enabled:
                ueye.is_DisableEvent(self.hCam, ueye.IS_SET_EVENT_FRAME)
        except Exception:
            pass

        try:
            ueye.is_StopLiveVideo(
                self.hCam,
                ueye.IS_FORCE_VIDEO_STOP,
            )
        except Exception:
            pass

        try:
            ueye.is_FreeImageMem(
                self.hCam,
                self.pcImageMemory,
                self.MemID,
            )
        except Exception:
            pass

        try:
            ueye.is_ExitCamera(self.hCam)
        except Exception:
            pass

        logger.info("IDS kamera kapatildi")

    def get_reported_fps(self):
        """Surucu tarafinin raporladigi FPS degerini dondurur (varsa)."""
        try:
            fps = ueye.double()
            ret = ueye.is_GetFramesPerSecond(self.hCam, fps)
            if ret == ueye.IS_SUCCESS:
                return float(fps.value)
        except Exception:
            pass
        return None


class FrameGrabber(QThread):
    """Kamera grab dongusunu UI'dan ayri bir thread'de yurutur. Yeni frame
    gelince Qt sinyali ile UI'a haber verir; UI ne kadar hizli render
    ederse etsin grab thread bagimsiz olarak surucuden veri alir."""

    frame_ready = pyqtSignal()
    error_occurred = pyqtSignal(str)

    def __init__(self, camera, parent=None):
        super().__init__(parent)
        self._camera = camera
        self._running = True
        self._mutex = QMutex()
        self._latest_full = None
        self._latest_preview = None

    def stop(self):
        self._running = False

    def latest(self):
        """UI thread'inin cagiracagi veri alma metodu (mutex korumali)."""
        with QMutexLocker(self._mutex):
            return self._latest_full, self._latest_preview

    def run(self):
        logger.debug("Grab thread basladi")
        while self._running:
            try:
                if not self._camera.wait_for_frame(timeout_ms=200):
                    continue

                frame = self._camera.get_frame()

                # Preview icin downscale (UI'da scale yapmaktan ucuz cunku
                # her frame buradaki tek bir resize ile hallediliyor).
                fh, fw = frame.shape[:2]
                if fw > MAX_PREVIEW_WIDTH:
                    ratio = MAX_PREVIEW_WIDTH / float(fw)
                    new_size = (MAX_PREVIEW_WIDTH, int(fh * ratio))
                    preview = cv2.resize(
                        frame,
                        new_size,
                        interpolation=cv2.INTER_LINEAR,
                    )
                else:
                    preview = frame

                # Frame ueye buffer'inda yasiyor; mutex altinda saglam bir
                # kopya tutalim ki UI thread renderlerken hicbir yere ucmasin.
                full_copy = np.array(frame, copy=True)
                preview_copy = np.array(preview, copy=True)

                with QMutexLocker(self._mutex):
                    self._latest_full = full_copy
                    self._latest_preview = preview_copy

                self.frame_ready.emit()
            except Exception as e:
                logger.exception("Grab thread hatasi")
                self.error_occurred.emit(str(e))
                break

        logger.debug("Grab thread sonlandi")


class CameraWindow(QWidget):
    """Kameradan gelen goruntuyu gosteren PyQt5 arayuz penceresi."""

    def __init__(self):
        """Arayuzu olusturur, kameraya baglanir ve yenileme timer'ini baslatir."""

        super().__init__()

        self.setWindowTitle("IDS Camera")

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.image_label.setMinimumSize(900, 600)

        self.fps_label = QLabel("FPS: 0")

        self.save_button = QPushButton("Fotoğraf Kaydet")
        self.save_button.clicked.connect(self.save_image)

        controls_layout = QHBoxLayout()
        controls_layout.addWidget(self.fps_label)
        controls_layout.addStretch(1)
        controls_layout.addWidget(self.save_button)

        layout = QVBoxLayout()
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        layout.addWidget(self.image_label, 1)
        layout.addLayout(controls_layout, 0)

        self.setLayout(layout)

        self.camera = None
        self.grabber = None
        self.frame_count = 0
        self._fps_last_query = 0.0
        self._cached_fps = None
        self._dirty = False

        self.connect_camera()
        if self.camera is None:
            raise RuntimeError("IDS kamerasi bulunamadi")

        # UI render timer'i: grab thread frame_ready emit edince _dirty=True
        # yapar, timer 30 Hz'de tek bir render yapar. Boylece grab cok hizli
        # olsa bile UI bogulmaz, kullanici akici bir preview gorur.
        self.timer = QTimer()
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self.render_latest)
        self.timer.start(UI_REFRESH_MS)
        logger.debug("UI timer baslatildi (%s ms)", UI_REFRESH_MS)

    def connect_camera(self):
        """Sadece IDS kameraya baglanir; baglanamazsa uygulama baslatilmaz."""
        logger.debug("Kamera baglantisi deneniyor")
        if not HAVE_UEYE:
            logger.error("pyueye / IDS SDK yuklu degil: %s", UEYE_IMPORT_ERROR)
            # parent=None: pencere henuz gorunmeyebilecegi icin top-level dialog kullaniyoruz.
            QMessageBox.critical(
                None,
                "IDS Driver Eksik",
                "IDS kamerası kullanılamıyor.\n\n"
                "Bu programın çalışması için bilgisayarda\n"
                "IDS Software Suite (uEye driver) kurulu olmalıdır.\n\n"
                "İndir: https://en.ids-imaging.com/download-ueye.html\n\n"
                f"Teknik detay: {UEYE_IMPORT_ERROR}",
            )
            self.camera = None
            return

        try:
            self.camera = IDS_Camera()
            logger.info("Aktif kamera surucusu: IDS")
        except Exception as e:
            logger.warning("IDS kamera baglanamadi: %s", e)
            QMessageBox.critical(
                None,
                "Kamera Bulunamadı",
                "Bağlı bir IDS kamera bulunamadı.\n\n"
                "Lütfen şunları kontrol edin:\n"
                "  • Kamera bilgisayara bağlı mı? (USB / Ethernet)\n"
                "  • Kameraya güç geliyor mu?\n"
                "  • Başka bir uygulama (IDS Camera Manager, uEye Cockpit)\n"
                "    kamerayı kullanıyor olabilir mi?\n"
                "  • IDS Camera Manager'da kamera görünüyor mu?\n\n"
                f"Teknik detay: {e}",
            )
            self.camera = None
            return

        # Grab thread'i baslat.
        self.grabber = FrameGrabber(self.camera, self)
        self.grabber.frame_ready.connect(self._on_frame_ready)
        self.grabber.error_occurred.connect(self._on_grab_error)
        self.grabber.start(QThread.HighPriority)

    def _on_frame_ready(self):
        """Grab thread yeni frame uretti. Sadece flag set ediyoruz; gercek
        cizim UI timer'inda yapiliyor (UI'i bogmamak icin)."""
        self._dirty = True

    def _on_grab_error(self, msg):
        logger.error("Grab thread hatasi: %s", msg)
        try:
            if self.camera:
                self.camera.close()
        except Exception:
            pass
        self.camera = None
        QMessageBox.critical(self, "Hata", f"Kamera baglantisi koptu: {msg}")
        QApplication.instance().quit()

    def render_latest(self):
        """En guncel frame'i ekrana cizer (UI thread, 30 Hz)."""
        if self.grabber is None or not self._dirty:
            return
        self._dirty = False

        full, preview = self.grabber.latest()
        if preview is None:
            return

        self.frame_count += 1

        # FPS okumayi 1 saniyede bir yap.
        now = time.monotonic()
        if now - self._fps_last_query >= FPS_QUERY_INTERVAL_S and self.camera is not None:
            self._cached_fps = self.camera.get_reported_fps()
            self._fps_last_query = now

        if self._cached_fps is not None:
            self.fps_label.setText(f"FPS (Driver): {self._cached_fps:.2f}")
        else:
            self.fps_label.setText("FPS (Driver): N/A")

        if self.frame_count % 60 == 0:
            logger.debug("Frame guncellendi: frame=%s driver_fps=%s", self.frame_count, self._cached_fps)

        if preview.ndim == 2:
            h, w = preview.shape
            qt_image = QImage(
                preview.data,
                w,
                h,
                w,
                QImage.Format_Grayscale8,
            )
        else:
            rgb = cv2.cvtColor(preview, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb.shape
            qt_image = QImage(
                rgb.data,
                w,
                h,
                ch * w,
                QImage.Format_RGB888,
            )

        pixmap = QPixmap.fromImage(qt_image)

        # Preview zaten dar, label boyutuna sigdirma cagrisi ek bir
        # smooth-scaling yapmiyor (FastTransformation): cok ucuz.
        self.image_label.setPixmap(
            pixmap.scaled(
                self.image_label.width(),
                self.image_label.height(),
                Qt.KeepAspectRatio,
                Qt.FastTransformation,
            )
        )

    def save_image(self):
        """Kullanici secilen dosya yoluna son full-res goruntuyu kaydeder."""
        if self.grabber is None:
            return
        full, _ = self.grabber.latest()
        if full is None:
            return

        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Kaydet",
            "",
            "PNG Files (*.png);;JPEG Files (*.jpg)",
        )

        if filename:
            cv2.imwrite(filename, full)
            logger.info("Goruntu kaydedildi: %s", filename)

    def closeEvent(self, event):
        """Pencere kapanirken thread ve kamera kaynaklarini serbest birakir."""
        logger.debug("Pencere kapatiliyor")
        try:
            if self.timer:
                self.timer.stop()
        except Exception:
            pass

        try:
            if self.grabber:
                self.grabber.stop()
                self.grabber.wait(2000)
        except Exception:
            pass

        try:
            if self.camera:
                self.camera.close()
        except Exception:
            pass

        event.accept()


if __name__ == "__main__":
    setup_logging()
    logger.info("Uygulama basladi")

    app = QApplication(sys.argv)

    try:
        window = CameraWindow()
        window.resize(1200, 800)
        window.show()
        sys.exit(app.exec_())
    except RuntimeError as e:
        # Ic katmanda gosterilmesi gereken uyari herhangi bir nedenle
        # gorunmediyse, kullanicinin bos ekranla kalmasi yerine son bir
        # bilgilendirme penceresi gosterelim.
        logger.error("Uygulama baslatilamadi: %s", e)
        QMessageBox.critical(
            None,
            "Uygulama Başlatılamadı",
            "Uygulama başlatılamadı.\n\n"
            "Genelde kamera bağlı değildir veya başka bir program tarafından kullanılıyordur.\n\n"
            f"Teknik detay: {e}",
        )
        sys.exit(1)
    except Exception as e:
        logger.exception("Beklenmeyen hata")
        QMessageBox.critical(
            None,
            "Beklenmeyen Hata",
            f"Uygulama beklenmeyen bir hata ile karşılaştı:\n\n{e}",
        )
        sys.exit(1)
