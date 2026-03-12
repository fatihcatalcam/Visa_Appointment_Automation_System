"""
BLS Spain Visa Bot - CAPTCHA Solver
OCR tabanlı sayı seçme CAPTCHA çözücü.

BLS sitesindeki CAPTCHA: 3x3 grid, her kutuda renkli/stilize sayı var.
"Please select all boxes with number XXX" → hedef sayıyla eşleşen kutuları tıkla.
"""

import os
import io
import re
import logging
import numpy as np
import base64
from PIL import Image, ImageFilter, ImageEnhance, ImageOps
import pytesseract
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

logger = logging.getLogger(__name__)

# Windows'ta Tesseract genellikle bu konumda
TESSERACT_PATHS = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Tesseract-OCR", "tesseract.exe"),
]


def _configure_tesseract():
    """Tesseract yolunu otomatik bul ve ayarla"""
    import os
    for path in TESSERACT_PATHS:
        if os.path.exists(path):
            pytesseract.pytesseract.tesseract_cmd = path
            logger.debug(f"Tesseract bulundu: {path}")
            return True
    # PATH'te varsa zaten çalışır
    logger.debug("Tesseract PATH'te aranıyor")
    return True


_configure_tesseract()


def _preprocess_cell_image(pil_img: Image.Image) -> Image.Image:
    """
    Tek bir CAPTCHA hücresini OCR için hazırla.
    Renkli, stilize sayıları daha iyi tanımak için çeşitli filtreler uygular.
    """
    # Büyüt (OCR için daha iyi)
    w, h = pil_img.size
    pil_img = pil_img.resize((w * 3, h * 3), Image.LANCZOS)

    # Gri tonlamaya çevir
    gray = pil_img.convert("L")

    # Kontrast artır
    enhancer = ImageEnhance.Contrast(gray)
    gray = enhancer.enhance(2.5)

    # Keskinleştir
    gray = gray.filter(ImageFilter.SHARPEN)
    gray = gray.filter(ImageFilter.SHARPEN)

    # Eşikleme (threshold) — sayıları arka plandan ayır
    # Adaptif eşikleme için numpy kullan
    arr = np.array(gray)
    # Ortalama parlaklığa göre ikili görüntü
    threshold = arr.mean() * 0.85
    binary = np.where(arr < threshold, 0, 255).astype(np.uint8)
    result = Image.fromarray(binary)

    return result


def _read_number_from_cell(pil_img: Image.Image) -> str:
    """
    Bir hücredeki sayıyı OCR ile oku.
    Birden fazla preprocessing stratejisi dener, en iyi sonucu döner.
    """
    candidates = []

    # Strateji 1: Direkt preprocessing
    processed = _preprocess_cell_image(pil_img)
    text = pytesseract.image_to_string(
        processed,
        config="--psm 8 --oem 3 -c tessedit_char_whitelist=0123456789"
    ).strip()
    candidates.append(text)

    # Strateji 2: Orijinal gri + büyütme
    gray_big = pil_img.convert("L").resize(
        (pil_img.width * 4, pil_img.height * 4), Image.LANCZOS
    )
    text2 = pytesseract.image_to_string(
        gray_big,
        config="--psm 8 --oem 3 -c tessedit_char_whitelist=0123456789"
    ).strip()
    candidates.append(text2)

    # Strateji 3: Ters çevrilmiş görüntü
    inverted = ImageOps.invert(processed)
    text3 = pytesseract.image_to_string(
        inverted,
        config="--psm 8 --oem 3 -c tessedit_char_whitelist=0123456789"
    ).strip()
    candidates.append(text3)

    # En uzun sayısal sonucu seç
    numbers = []
    for c in candidates:
        digits = re.sub(r"\D", "", c)
        if digits:
            numbers.append(digits)

    if not numbers:
        return ""

    # En sık görülen veya en uzun olanı döndür
    from collections import Counter
    count = Counter(numbers)
    best = count.most_common(1)[0][0]
    logger.debug(f"OCR adayları: {candidates} → seçilen: {best}")
    return best


class CaptchaSolver:
    """
    BLS sitesindeki özel sayı-seçme CAPTCHA'sını çözer.

    CAPTCHA yapısı:
    - Talimat metni: "Please select all boxes with number XXX"
    - 3x3 grid (9 hücre), her birinde renkli sayı
    - Hedef sayıyla eşleşen tüm hücrelere tıklanmalı
    """

    # Olası CAPTCHA container CSS seçicileri
    CAPTCHA_SELECTORS = [
        "div.captcha-container",
        "div[class*='captcha']",
        "div[id*='captcha']",
        "table.captcha",
        "div.captcha",
        "#captchaDiv",
        ".captcha-grid",
    ]

    # Talimat metni seçicileri
    INSTRUCTION_SELECTORS = [
        "p[class*='captcha']",
        "div[class*='captcha'] p",
        "div[class*='captcha'] span",
        "label[class*='captcha']",
        ".captcha-instruction",
        "p:contains('select all boxes')",
    ]

    def __init__(self, driver, api_key: str = None):
        self.driver = driver
        self.wait = WebDriverWait(driver, 10)
        self.api_key = api_key

    def is_captcha_present(self) -> bool:
        """Sayfada CAPTCHA var mı kontrol et"""
        try:
            page_text = self.driver.find_element(By.TAG_NAME, "body").text.lower()
            return "select all boxes with number" in page_text
        except Exception:
            return False

    def solve(self) -> bool:
        """
        CAPTCHA'yı çöz ve submit et.
        Returns: True if solved successfully
        """
        try:
            logger.info("CAPTCHA tespit edildi, çözülüyor...")

            # API Key varsa 2Captcha dene
            if self.api_key:
                logger.info("2Captcha servisi kullanılıyor...")
                if self.solve_with_2captcha(self.api_key):
                    return True
                logger.warning("2Captcha başarısız oldu, yerel OCR deneniyor...")

            # Fallback: Yerel OCR
            return self._solve_local_ocr()

        except Exception as e:
            logger.error(f"CAPTCHA çözme hatası: {e}")
            return False

    def _read_number_from_cell(self, img_pil):
        """
        Verilen PIL görselinden OCR ile sayı okur.
        Daha hassas bir ön işleme (Resize, Kontrast, Keskinlik) yapar.
        Açık renkli sayıları (sarı/yeşil) korumak için eşikleme kaldırıldı/optimize edildi.
        """
        try:
            # 1. Büyüt (Daha iyi okuma için, 3x daha iyi)
            img = img_pil.resize((img_pil.width * 3, img_pil.height * 3), Image.Resampling.LANCZOS)
            
            # 2. Gri Tonlama
            img = img.convert('L') # 0-255 arası gri
            
            # 3. Kontrast Artır (Açık renkleri belirginleştir)
            enhancer = ImageEnhance.Contrast(img)
            img = enhancer.enhance(2.0)
            
            # 4. Keskinlik Artır (Kenarları belirginleştir)
            enhancer = ImageEnhance.Sharpness(img)
            img = enhancer.enhance(2.0) # Kenarları keskinleştir

            # 5. Eşikleme (Threshold) - Çok agresif yapma!
            # Beyaz arkaplan (~255), Yazı (0-200 arası).
            # Eğer threshold 200 yaparsak, 201 olan hafif gri beyaz olur, 199 siyah olur.
            # Yazı rengi sarı ise (Parlak), gri değeri yüksek olabilir (örn 220).
            # O yüzden threshold kullanmak riskli olabilir.
            # Tesseract gri tonlamayı da okuyabilir.
            # Sadece çok gürültülü ise threshold lazım.
            # Şimdilik sadece Autocontrast + Grayscale deneyelim.
            img = ImageOps.autocontrast(img)
            
            # Debug için kaydet (Gerekirse açılabilir)
            # img.save("debug_cell_ocr.png")

            # 6. Tesseract ile Oku
            # config: Sadece rakamları okumaya zorla (--psm 6: Tek blok metin)
            custom_config = r'--oem 3 --psm 6 -c tessedit_char_whitelist=0123456789'
            text = pytesseract.image_to_string(img, config=custom_config)
            
            # Temizle
            return "".join([c for c in text if c.isdigit()])
        except Exception as e:
            logging.error(f"OCR Hatası: {e}")
            return ""

    def _find_captcha_cells(self, container_element=None):
        """
        Captcha hücrelerini (div.col-4) bulur ve GÖRSEL SIRAYA dizer.
        
        Temel sorun: site eski CAPTCHA hücrelerini DOM'dan silmiyor,
        üst üste yığıyor. Tüm hücreler aynı screen koordinatında.
        
        Çözüm: Her benzersiz (Top, Left) grid pozisyonu için
        EN SON DOM ELEMANINI seç (en son eklenen = aktif CAPTCHA).
        """
        try:
            raw_cells = []
            if container_element:
                try:
                    raw_cells = container_element.find_elements(By.CSS_SELECTOR, "div.col-4")
                except Exception as e:
                    logger.debug(f"Container hücre arama hatası: {e}")
            if not raw_cells:
                try:
                    raw_cells = self.driver.find_elements(By.CSS_SELECTOR, "div.col-4")
                except Exception as e:
                    logger.debug(f"Genel arama hatası: {e}")

            if not raw_cells:
                return []

            logger.info(f"   Toplam div.col-4 sayısı: {len(raw_cells)}")

            # Her hücrenin screen pozisyonunu al
            # img.captcha-img içermeyen ve boyutsuz olanları filtrele
            # PERF: Batch JS — 1 call instead of N individual calls
            cells_with_rect = []
            try:
                all_rects = self.driver.execute_script("""
                    var cells = arguments[0];
                    var results = [];
                    for (var i = 0; i < cells.length; i++) {
                        var el = cells[i];
                        var rect = el.getBoundingClientRect();
                        var style = window.getComputedStyle(el);
                        if (rect.width < 10 || rect.height < 10) { results.push(null); continue; }
                        if (style.display === 'none') { results.push(null); continue; }
                        if (el.querySelectorAll('img.captcha-img').length === 0) { results.push(null); continue; }
                        results.push({top: Math.round(rect.top), left: Math.round(rect.left),
                                w: rect.width, h: rect.height});
                    }
                    return results;
                """, raw_cells)
                for i, data in enumerate(all_rects or []):
                    if data:
                        cells_with_rect.append((raw_cells[i], data['top'], data['left']))
            except Exception:
                pass

            logger.info(f"   Geçerli hücre sayısı (img içeren, boyutlu): {len(cells_with_rect)}")

            if not cells_with_rect:
                # Fallback: sadece is_displayed kontrolü
                fallback = [c for c in raw_cells if c.is_displayed() and c.size.get('width', 0) > 10]
                return fallback[-9:] if len(fallback) > 9 else fallback

            # POZİSYON BAZLI DEDUPLİCATION
            # Aynı (Top, Left) konumundaki hücrelerden SADECE EN SONUNCUSUNU tut.
            # Çünkü DOM sırasında en son = en yeni eklenen = aktif CAPTCHA hücresi.
            # 20px tolerans ile gruplama (küçük render farklılıklarını tolere et)
            TOLERANCE = 20
            position_map = {}  # (top_rounded, left_rounded) -> son cell
            for cell, top, left in cells_with_rect:
                top_key = round(top / TOLERANCE) * TOLERANCE
                left_key = round(left / TOLERANCE) * TOLERANCE
                position_map[(top_key, left_key)] = cell  # Her seferinde üstüne yaz = son eleman kalır

            unique_cells = list(position_map.values())
            logger.info(f"   Benzersiz pozisyon sayısı (dedup sonrası): {len(unique_cells)}")

            if len(unique_cells) < 4:
                logger.warning(f"Yetersiz benzersiz hücre ({len(unique_cells)}), son 9 fallback.")
                unique_cells = [c for c, _, _ in cells_with_rect[-9:]]

            # Pozisyona göre sırala (satır x sütun = sol-üst'ten sağ-alt'a)
            # PERF: Batch JS for sorting rects — 1 call instead of N
            cells_with_pos = []
            try:
                sort_rects = self.driver.execute_script("""
                    var cells = arguments[0];
                    return cells.map(function(el) {
                        var r = el.getBoundingClientRect();
                        return {top: r.top, left: r.left};
                    });
                """, unique_cells)
                for i, rect in enumerate(sort_rects or []):
                    cells_with_pos.append((unique_cells[i], rect.get('top', 0), rect.get('left', 0)))
            except Exception:
                cells_with_pos = [(c, 0, 0) for c in unique_cells]

            cells_with_pos.sort(key=lambda x: (round(x[1] / TOLERANCE) * TOLERANCE, x[2]))
            sorted_cells = [c for c, _, _ in cells_with_pos]

            log_msg = f"   📍 Hücre Sıralaması ({len(sorted_cells)} hücre, Top/Left):\n"
            for i, (c, top, left) in enumerate(cells_with_pos):
                log_msg += f"      [{i+1}] -> Top:{top:.0f}, Left:{left:.0f}\n"
            logger.debug(log_msg)

            return sorted_cells[:9]  # Max 9 hücre

        except Exception as e:
            logger.error(f"Hücre bulma hatası: {e}", exc_info=True)
            return []



    def _find_captcha_context(self) -> tuple:
        """
        Aktif Captcha konteynerini ve hedef sayıyı bulur.
        Çoklu/Yığılmış (Stacked) Captcha durumunda en son eklenen ve görünür olanı seçer.
        """
        try:
            import re
            logger.info("🔍 Captcha bağlamı aranıyor (Konteyner Taraması)...")

            # 1. Potansiyel Konteynerleri Bul (ID veya Sınıf ile)
            # Genellikle 'captcha-main-div' ID'si veya 'main-div-container' sınıfı kullanılır.
            # Sayfada birden fazla aynı ID'li eleman olabilir!
            containers = self.driver.find_elements(By.XPATH, "//div[contains(@class, 'main-div-container') or @id='captcha-main-div']")
            
            logger.info(f"   Bulunan toplam potansiyel konteyner sayısı: {len(containers)}")

            # 2. Tersten Tara (En son eklenen genellikle aktiftir)
            for i, container in enumerate(reversed(containers)):
                idx = len(containers) - 1 - i
                try:
                    # Görünürlük Kontrolü
                    if not container.is_displayed():
                        logger.debug(f"   [Konteyner {idx}] Görünür değil (is_displayed=False). Atlanıyor.")
                        continue

                    # Boyut Kontrolü (Boş veya gizli konteynerleri ele)
                    size = container.size
                    if size['width'] < 200 or size['height'] < 100:
                        logger.debug(f"   [Konteyner {idx}] Boyut yetersiz ({size}). Atlanıyor.")
                        continue
                    
                    # CSS Görünürlük Kontrolü
                    try:
                        opacity = container.value_of_css_property('opacity')
                        visibility = container.value_of_css_property('visibility')
                        if opacity == '0' or visibility == 'hidden':
                            logger.debug(f"   [Konteyner {idx}] CSS ile gizlenmiş (Op:{opacity}, Vis:{visibility}). Atlanıyor.")
                            continue
                    except:
                        pass

                    # 3. Konteyner İçindeki Metni Bul
                    # "Please select all boxes with number X" metnini ara
                    txt_elements = container.find_elements(By.XPATH, ".//*[contains(text(), 'Please select all boxes')]")
                    
                    target_text = None
                    # Konteyner içinde birden fazla metin olabilir (yine yığılmışsa), en sonuncuyu al
                    visible_txts = []
                    for txt in txt_elements:
                        if txt.is_displayed():
                            try:
                                t_op = txt.value_of_css_property('opacity')
                                t_vis = txt.value_of_css_property('visibility')
                                if t_op != '0' and t_vis != 'hidden':
                                    visible_txts.append(txt)
                            except:
                                visible_txts.append(txt)
                    
                    if not visible_txts:
                        logger.debug(f"   [Konteyner {idx}] İçinde görünür talimat metni yok. Atlanıyor.")
                        continue
                    
                    # 4. En iyi metni seç (Z-Index ve Y-Konumuna göre)
                    best_text_el = None
                    best_score = -999999
                    
                    logger.info(f"   [Konteyner {idx}] {len(visible_txts)} adet metin adayı inceleniyor:")
                    
                    for t in visible_txts:
                        try:
                            # Metin
                            txt_content = t.text.strip()
                            # Konum
                            loc = t.location
                            y_pos = loc['y'] if loc else 999999
                            # Z-Index
                            z_idx = 0
                            try:
                                z_val = t.value_of_css_property('z-index')
                                if z_val and z_val != 'auto':
                                    z_idx = int(z_val)
                            except:
                                z_idx = 0
                            
                            # Puanlama: Yüksek Z-Index iyi, Düşük Y (Yukarıda) iyi
                            # Y ne kadar küçükse (yukarıda) o kadar iyi -> -y_pos
                            # Z ne kadar büyükse o kadar iyi -> z_idx * 1000
                            # Ancak burada 'Overflow' ihtimaline karşı EN ÜSTTEKİ (Y'si en küçük) olanı seçmek mantıklı olabilir
                            # eğer Z-indexleri aynıysa.
                            
                            score = (z_idx * 10000) - y_pos
                            
                            logger.debug(f"      -> Aday: '{txt_content}' | Y: {y_pos} | Z: {z_idx} | Score: {score}")
                            
                            if score > best_score:
                                best_score = score
                                best_text_el = t
                        except Exception as text_err:
                            logger.debug(f"      Text analiz hatası: {text_err}")
                            continue

                    if not best_text_el:
                        logger.warning(f"   [Konteyner {idx}] Uygun metin seçilemedi.")
                        continue

                    target_text = best_text_el.text.strip()
                    
                    # Sayıyı Ayıkla - STRICT REGEX
                    # Hatalı alımları önlemek için 'number X', 'with X', 'numara X' kalıplarına öncelik ver.
                    # Eğer sadece sayı varsa onu al ama etrafına bak.
                    
                    num = None
                    # Kalıplar (En güvenliden genele)
                    patterns = [
                        r"(?:number|numara|sayı)\D+?(\d+)",  # number ... 123
                        r"(?:with|ile)\D+?(\d+)",           # with ... 123
                        r"(\d+)"                            # Fallback: Herhangi bir sayı (Riskli ama son çare)
                    ]
                    
                    for p in patterns:
                        match = re.search(p, target_text, re.IGNORECASE)
                        if match:
                            # Bulunan sayıyı al
                            candidate = match.group(1)
                            # Basit kontrol: 0 ile bariz yanlışları ele? (Genelde 3 basamaklı olur ama bazen 2)
                            # Tarih/Saat gibi şeyleri elemek zor ama 'number' keywordu varsa güvenlidir.
                            if 'number' in target_text.lower() or 'with' in target_text.lower() or 'select' in target_text.lower():
                                num = candidate
                                break
                            # Keyword yoksa ve sadece sayı bulduysa, biraz şüpheci yaklaş (Timer olabilir)
                            if len(candidate) == 3 or len(candidate) == 2: # Genelde 2-3 haneli
                                num = candidate
                                break

                    if num:
                        logger.info(f"✅ [Konteyner {idx}] AKTİF ADAY SEÇİLDİ! Metin: '{target_text}', Sayı: {num}")
                        return container, num
                    else:
                        logger.warning(f"   [Konteyner {idx}] Metin var ama sayı kalıba uymuyor: '{target_text}'")

                except Exception as e:
                    logger.error(f"   [Konteyner {idx}] İnceleme hatası: {e}")
                    continue

            logger.error("❌ Hiçbir geçerli Captcha konteyneri bulunamadı!")
            return None, None

        except Exception as e:
            logger.error(f"❌ _find_captcha_context hatası: {e}")
            return None, None

    def _find_captcha_context_fallback(self) -> tuple:
        """Eski yöntem: Metin üzerinden bulma (Son çare)"""
        try:
            # Geniş arama kullan
            els = self.driver.find_elements(By.XPATH, "//*[contains(., 'Please select all boxes')]")
            logger.info(f"Fallback text arama sonucu: {len(els)}")
            
            valid_candidates = []
            
            for i, el in enumerate(els):
                try:
                    if el.tag_name in ['html', 'body', 'script']: continue
                    
                    is_vis = el.is_displayed()
                    op = "1"
                    try: op = el.value_of_css_property('opacity') 
                    except: pass

                    if is_vis and op != '0':
                        text = el.text.strip()
                        import re
                        m = re.search(r'\d+', text)
                        if m:
                            num = int(m.group(0))
                            
                            container = el.find_element(By.XPATH, "./..")
                            temp = container
                            has_visible_images = False
                            
                            for _ in range(3):
                                imgs = temp.find_elements(By.TAG_NAME, "img")
                                if len(imgs) >= 4:
                                    visible_imgs = [img for img in imgs if img.is_displayed()]
                                    if len(visible_imgs) >= 4:
                                        has_visible_images = True
                                        container = temp
                                        break
                                try:
                                    parent = temp.find_element(By.XPATH, "./..")
                                    if parent.tag_name == "html": break
                                    temp = parent
                                except:
                                    break
                            
                            size = container.size
                            if has_visible_images and size['height'] > 100 and size['width'] > 200:
                                valid_candidates.append({
                                    'num': num,
                                    'container': container,
                                    'index': i,
                                    'size': size
                                })
                except:
                    pass

            if valid_candidates:
                # En sonuncuyu seç (DOM sırası)
                best = valid_candidates[-1]
                logger.info(f"✅ CAPTCHA Context Bulundu (Fallback): Hedef={best['num']}, Size={best['size']}")
                return best['container'], best['num']
                
        except Exception as e:
            logger.debug(f"Fallback hatası: {e}")
        
        logger.error("❌ CAPTCHA context hiçbir yöntemle bulunamadı!")
        return None, None

    def _get_cell_image_from_html(self, cell_element) -> Image.Image:
        """
        Hücrenin o anki görsel halini alır.
        
        ÖNCELİK: Selenium element screenshot (tarayıcıda GERÇEKTE görünen frame).
        Bu sayede GIF animasyonunun doğru frame'i yakalanır.
        FALLBACK: base64 src okuma (GIF'in ilk frame'i, hatalı olabilir).
        """
        import io as _io
        # 1. Önce Selenium screenshot - tarayıcıda o an görünen içeriği yakalar
        try:
            img_el = cell_element.find_element(By.CSS_SELECTOR, "img.captcha-img")
            png = img_el.screenshot_as_png
            if png and len(png) > 100:
                return Image.open(_io.BytesIO(png)).convert("RGB")
        except Exception as e:
            logger.debug(f"Element screenshot başarısız: {e}")

        # 2. Fallback: base64 src okuma (GIF ilk frame - hatalı olabilir)
        try:
            img_el = cell_element.find_element(By.CSS_SELECTOR, "img.captcha-img")
            src = img_el.get_attribute("src") or ""
            if ";base64," in src:
                b64_data = src.split(";base64,", 1)[1]
                raw = base64.b64decode(b64_data)
                logger.debug("Fallback: base64 src kullanıldı (GIF ilk frame)")
                return Image.open(_io.BytesIO(raw)).convert("RGB")
        except Exception as e2:
            logger.error(f"Base64 de alınamadı: {e2}")

        return None


    def _build_grid_image(self, cell_images: list, target_text: str = None, cols=3) -> Image.Image:
        """
        Hücre görsellerini 3x3 grid olarak birleştirir ve üzerine talimat yazar.
        Görselleri iyileştirir (Autocontrast) ve referans numaralarını ekler.
        """
        try:
            from PIL import ImageDraw, ImageFont, ImageOps
        except ImportError:
            logger.warning("PIL ImageDraw/Font eksik")
            return None

        cell_size = 130 # Hücreleri biraz daha büyüt (Netlik için)
        rows = (len(cell_images) + cols - 1) // cols
        
        grid_w = cols * cell_size
        grid_h = rows * cell_size
        
        header_h = 80 if target_text else 0
        total_h = grid_h + header_h

        final_img = Image.new("RGB", (grid_w, total_h), (255, 255, 255))
        
        # --- HEADER ---
        if target_text:
            draw = ImageDraw.Draw(final_img)
            font_header = None
            try:
                font_paths = ["arialbd.ttf", "arial.ttf", "seguisb.ttf", "calibrib.ttf"]
                for fp in font_paths:
                    try:
                        font_header = ImageFont.truetype(fp, 40)
                        break
                    except: pass
            except: pass
            if not font_header:
                font_header = ImageFont.load_default()

            try:
                bbox = draw.textbbox((0, 0), target_text, font=font_header)
                text_w = bbox[2] - bbox[0]
                text_h = bbox[3] - bbox[1]
            except AttributeError:
                text_w, text_h = draw.textsize(target_text, font=font_header)
                
            x_text = (grid_w - text_w) // 2
            y_text = (header_h - text_h) // 2
            
            draw.text((x_text, y_text), target_text, fill=(255, 0, 0), font=font_header)
            draw.line([(0, header_h-2), (grid_w, header_h-2)], fill="black", width=3)

        # --- CELLS ---
        # Referans numarası fontu (Küçük ve zarif)
        font_ref = None
        try:
             font_ref = ImageFont.truetype("arial.ttf", 20)
        except:
             font_ref = ImageFont.load_default()

        for idx, img in enumerate(cell_images):
            row = idx // cols
            col = idx % cols
            x = col * cell_size
            y = header_h + (row * cell_size)

            if img is None:
                # Placeholder
                draw = ImageDraw.Draw(final_img)
                draw.rectangle([x, y, x+cell_size, y+cell_size], fill="#EEEEEE")
                continue
            
            # GÖRSEL İYİLEŞTİRME
            # 1. Resize (Büyüt)
            img_resized = img.resize((cell_size, cell_size), Image.LANCZOS)
            
            # 2. Auto Contrast (Rakamları belirginleştir)
            try:
                # Sadece RGB modunda çalışır
                if img_resized.mode != 'RGB':
                    img_resized = img_resized.convert('RGB')
                img_resized = ImageOps.autocontrast(img_resized, cutoff=1)
            except Exception as e:
                pass # Hata olursa orijinal kalsın

            final_img.paste(img_resized, (x, y))
            
            draw = ImageDraw.Draw(final_img)
            # Çerçeve
            draw.rectangle([x, y, x+cell_size, y+cell_size], outline="#BBBBBB", width=1)
            
            # Referans Numarası (Köşeye, küçük, mavi)
            # Rakamın arkasına ufak beyaz gölge/bg atalım ki karışmasın
            ref_txt = str(idx + 1)
            draw.rectangle([x+1, y+1, x+25, y+25], fill="white", outline=None)
            draw.text((x+8, y+2), ref_txt, fill="blue", font=font_ref)

        return final_img

    def solve_with_2captcha(self, api_key: str, retries=2) -> bool:
        """Multipart Grid Yöntemi"""
        import time
        import io as _io
        try:
            import requests as _req
        except ImportError:
            logger.warning("requests eksik")
            return False

        for attempt in range(retries + 1):
            try:
                # 1. Context
                container, target_number = self._find_captcha_context()
                if not container:
                    container, target_number = self._find_captcha_context_fallback()
                
                if not target_number:
                    target_number = self._get_target_number()
                
                if not target_number:
                    logger.error("Hedef sayı bulunamadı.")
                    time.sleep(1)
                    continue

                target_str = str(target_number).strip()
                instruction_text = f"Select all images with number {target_str}"
                logger.info(f"🎯 Hedef: {target_str} (Deneme {attempt+1})")

                # 2. Hücreler
                cells = self._find_captcha_cells(container_element=container)
                if len(cells) < 9:
                    time.sleep(0.5)
                    continue
                cells = cells[:9]

                # 3. Görseller
                cell_images = []
                for idx, cell in enumerate(cells):
                    cell_images.append(self._get_cell_image_from_html(cell))
                
                if all(i is None for i in cell_images):
                    continue

                # 4. Grid + Header
                grid_img = self._build_grid_image(cell_images, target_text=instruction_text)
                if not grid_img: continue
                
                # Bytes (Skipped file save to remove I/O bottleneck)
                buf = _io.BytesIO()
                grid_img.save(buf, format="PNG")
                img_bytes = buf.getvalue()

                # 5. Gönder
                logger.info("   🚀 2Captcha'ya gönderiliyor...")
                files = {'file': ('captcha.png', img_bytes, 'image/png')}
                data = {
                    'key': api_key, 'method': 'post', 'recaptcha': '1',
                    'json': '1', 'textinstructions': instruction_text,
                    'lang': 'en', 'soft_id': '2370'
                }
                
                try:
                    resp = _req.post("https://2captcha.com/in.php", files=files, data=data, timeout=30)
                    rj = resp.json()
                except Exception as e:
                    logger.error(f"Network hatası: {e}")
                    time.sleep(1)
                    continue

                if rj.get("status") != 1:
                    logger.error(f"API Hatası: {rj.get('request')}")
                    time.sleep(1)
                    continue

                req_id = rj.get("request")
                logger.info(f"   ✅ ID: {req_id}. Bekleniyor...")

                # 6. Sonuç
                # PERF: Adaptive polling — start at 2s, backoff to max 5s
                indices = []
                poll_delay = 2.0
                for poll_attempt in range(25):
                    time.sleep(poll_delay)
                    try:
                        r = _req.get(f"https://2captcha.com/res.php?key={api_key}&action=get&id={req_id}&json=1", timeout=10)
                        j = r.json()
                        if j.get("status") == 1:
                            ans = j.get("request", "").replace("click:", "")
                            indices = [int(n)-1 for n in re.findall(r"\d+", ans)]
                            logger.info(f"   📩 Cevap: {ans} (poll #{poll_attempt+1})")
                            break
                        elif j.get("request") == "CAPCHA_NOT_READY":
                            poll_delay = min(poll_delay + 0.5, 5.0)
                            continue
                        else:
                            break
                    except: continue
                
                if not indices:
                    logger.warning("Cevap alınamadı.")
                    continue

                logger.info(f"   🎯 Tıklanacaklar: {[i+1 for i in indices]}")

                # 7. Tıkla (Human-like)
                from selenium.webdriver.common.action_chains import ActionChains
                import random

                # PERF: Reuse cells list; only re-find on StaleElementReferenceException
                from selenium.common.exceptions import StaleElementReferenceException
                clicked_cnt = 0
                for idx in indices:
                    if 0 <= idx < len(cells):
                        try:
                            c = cells[idx]

                            # 1. Scroll
                            try:
                                self.driver.execute_script("arguments[0].scrollIntoView({block: 'center', inline: 'nearest'});", c)
                            except StaleElementReferenceException:
                                cells = self._find_captcha_cells(container_element=container)
                                if len(cells) > idx:
                                    c = cells[idx]
                                    self.driver.execute_script("arguments[0].scrollIntoView({block: 'center', inline: 'nearest'});", c)
                                else:
                                    continue
                            time.sleep(random.uniform(0.1, 0.2))
                            
                            # 2. ActionChains ile Tıkla (Gerçek Mouse Olayı)
                            try:
                                actions = ActionChains(self.driver)
                                actions.move_to_element(c).click().perform()
                                logger.info(f"      🖱️ ActionClick: Hücre {idx+1}")
                            except:
                                # Fallback: JS Click
                                self.driver.execute_script("arguments[0].click();", c)
                                logger.info(f"      🔧 JS Click (Fallback): Hücre {idx+1}")

                            # 3. Görsel İşaretleme
                            self.driver.execute_script("arguments[0].style.border='4px solid green';", c)
                            clicked_cnt += 1
                            
                            # PERF: Reduced inter-click delay (was 0.5-1.2s)
                            time.sleep(random.uniform(0.15, 0.4))
                            
                        except Exception as click_err:
                            logger.error(f"Hücre {idx} tıklama hatası: {click_err}")
                
                logger.info(f"   ✓ {clicked_cnt} hücre tıklandı.")
                
                # PERF: Reduced pre-submit wait (was 3s, JS propagation is instant)
                time.sleep(1)

                # 8. Submit (Sadece Captcha Container içindeki veya yakınındaki)
                if self._click_submit(container_scope=container):
                    logger.info("🎉 CAPTCHA çözüldü (Submit OK).")
                    return True

            except Exception as e:
                logger.error(f"Döngü hatası: {e}")
                time.sleep(1)
        
        return False

    def _click_submit(self, container_scope=None) -> bool:
        """
        CAPTCHA submit butonuna tıkla.
        ÖNCELİK: Konteyner içindeki 'Verify'/'Submit' butonları (Genelde Yeşil).
        """
        import time

        found_btn = None
        
        # A. Konteyner İçi Arama (En Güvenli)
        if container_scope:
            try:
                # Genelde BLS'de yeşil buton 'btn-primary' veya 'btn-success' classına sahip olabilir
                # Veya sadece text 'Submit'tir.
                # XPath: Konteyner içindeki butonlardan text'i Submit/Verify olan
                potential_xpaths = [
                    ".//button[contains(translate(text(),'SUBMIT','submit'), 'submit')]",
                    ".//button[contains(translate(text(),'VERIFY','verify'), 'verify')]",
                    ".//a[contains(@class,'btn') and contains(translate(text(),'SUBMIT','submit'), 'submit')]",
                    ".//input[@type='submit']"
                ]
                
                for xp in potential_xpaths:
                    btns = container_scope.find_elements(By.XPATH, xp)
                    for btn in btns:
                        if btn.is_displayed():
                            found_btn = btn
                            logger.info(f"✅ Container içi buton bulundu: {xp}")
                            break
                    if found_btn: break
            except Exception as e:
                logger.debug(f"Container scope submit hatası: {e}")

        # B. Eğer container içinde bulamadıysak, Container'ın komşularına bak (Bazen buton footer'dadır ve container sadece grid'dir)
        if not found_btn and container_scope:
             try:
                 # Container'ın parent'ına çık ve orda ara
                 parent = container_scope.find_element(By.XPATH, "..")
                 btns = parent.find_elements(By.CSS_SELECTOR, "button.btn-primary, button.btn-success, button")
                 for btn in btns:
                     if not btn.is_displayed(): continue
                     txt = (btn.text or "").lower()
                     if "submit" in txt or "verify" in txt:
                         # Login butonu olmasın!
                         if "login" in str(btn.get_attribute("id")).lower(): continue
                         
                         found_btn = btn
                         logger.info("✅ Parent içinde Submit butonu bulundu")
                         break
             except: pass

        # C. Genel Arama (Dikkatli ol - Login butonuna basma!)
        if not found_btn:
            selectors = [
                ".captcha-submit", "#captchaSubmit", "button.btn-captcha",
                "a[onclick*='NewCaptchaSubmit']", # BLS Özel fonksiyonu olabilir
                "button[onclick*='Submit']:not([id*='login'])" # Login olmayan submit
            ]
            
            for sel in selectors:
                try:
                    els = self.driver.find_elements(By.CSS_SELECTOR, sel)
                    for el in els:
                        if el.is_displayed():
                            found_btn = el
                            logger.info(f"✅ Genel buton bulundu ({sel})")
                            break
                    if found_btn: break
                except: pass

        # --- TIKLAMA İŞLEMİ ---
        if found_btn:
            try:
                # Önce native click (JS eventlerini daha iyi tetikler)
                found_btn.click()
                logger.info("🖱️ Native click yapıldı.")
            except:
                # Hata verirse JS click
                self.driver.execute_script("arguments[0].click();", found_btn)
                logger.info("🔧 JS click yapıldı (Fallback).")
            
            time.sleep(1)
            return True
        else:
            logger.warning("⚠️ Captcha özel submit butonu bulunamadı.")
            return False # Login akışına bırakma, çünkü bu Captcha özel submit



    def _solve_local_ocr(self) -> bool:
        """Eski OCR yöntemi (Yedek)"""
        try:
            # 1. Context (Container + Sayı) bul
            container, target_number = self._find_captcha_context()
            
            if not target_number:
                target_number = self._get_target_number()
            
            if not target_number:
                logger.error("CAPTCHA hedef sayısı okunamadı")
                return False

            logger.info(f"CAPTCHA hedef sayısı: {target_number} (OCR)")

            # 2. Hücreleri bul (Container varsa onun içinde ara)
            cells = self._find_captcha_cells(container_element=container)
            
            # ... (Original Loop logic moved here or kept)
            # Since I am replacing the file, I need to include the rest of logic or call existing methods?
            # I will paste the original logic inside _solve_local_ocr
            
            if not cells:
                logger.error("CAPTCHA hücreleri bulunamadı")
                return False

            clicked = 0
            for i, cell in enumerate(cells):
                cell_number = self._read_cell(cell, i)
                if cell_number == target_number:
                    try:
                        self.driver.execute_script("arguments[0].click();", cell)
                        clicked += 1
                        time.sleep(0.2)
                    except Exception as e:
                        pass

            if clicked == 0:
                logger.warning("OCR Hiçbir hücre eşleşmedi")
                return False

            return self._click_submit()
        except Exception as e:
             logger.error(f"OCR hatası: {e}")
             return False

    # (Keep _get_target_number, _find_captcha_cells, _read_cell, _click_submit as helper methods)


    def _get_target_number(self) -> str:
        """
        "Please select all boxes with number 531" metninden hedef sayıyı çıkar
        """
        try:
            body_text = self.driver.find_element(By.TAG_NAME, "body").text
            # "number XXX" pattern'ı ara
            match = re.search(r"number\s+(\d+)", body_text, re.IGNORECASE)
            if match:
                return match.group(1).strip()

            # Alternatif: "select all boxes with XXX"
            match2 = re.search(r"with\s+(\d+)", body_text, re.IGNORECASE)
            if match2:
                return match2.group(1).strip()

        except Exception as e:
            logger.error(f"Hedef sayı okunamadı: {e}")
        return ""



    def _is_in_viewport(self, element) -> bool:
        """
        Elementin gerçekten görünür ve viewport içinde olup olmadığını kontrol et.
        display:none, visibility:hidden, opacity:0, off-screen olanları filtreler.
        """
        try:
            result = self.driver.execute_script("""
                var el = arguments[0];
                var rect = el.getBoundingClientRect();
                var style = window.getComputedStyle(el);
                return (
                    rect.width > 0 &&
                    rect.height > 0 &&
                    style.display !== 'none' &&
                    style.visibility !== 'hidden' &&
                    style.opacity !== '0' &&
                    el.offsetParent !== null &&
                    rect.top >= -50 &&
                    rect.left >= -50 &&
                    rect.bottom <= (window.innerHeight || document.documentElement.clientHeight) + 200 &&
                    rect.right  <= (window.innerWidth  || document.documentElement.clientWidth)  + 200
                );
            """, element)
            return bool(result)
        except Exception:
            return False

    def _read_cell(self, cell_element, index: int) -> str:
        """
        Bir CAPTCHA hücresini screenshot alıp OCR ile oku.
        """
        try:
            # Yöntem 1: Elementin içindeki text (bazı implementasyonlarda metin direkt var)
            cell_text = cell_element.text.strip()
            if cell_text and re.match(r"^\d+$", cell_text):
                return cell_text

            # Yöntem 2: Screenshot al ve OCR uygula
            screenshot_bytes = cell_element.screenshot_as_png
            img = Image.open(io.BytesIO(screenshot_bytes))

            if img.width < 5 or img.height < 5:
                return ""

            number = _read_number_from_cell(img)
            return number

        except Exception as e:
            logger.debug(f"Hücre {index} okunamadı: {e}")
            return ""


