# -*- coding: utf-8 -*-
"""Extracted module app_config."""
from pathlib import Path
import sys

from PySide6.QtCore import QTranslator
from PySide6.QtGui import QFontDatabase

BASE_DIR = Path(__file__).resolve().parent
APP_DIR = BASE_DIR
ICON_PATH = BASE_DIR / "icons" / "icon.ico"
FONTS_DIR = BASE_DIR / "fonts"
TEMPLATE_PATH = BASE_DIR / "template.odt"

def get_db_path():
    """Return the database path beside the application, independent of cwd."""
    if getattr(sys, 'frozen', False):
        base_dir = Path(sys.executable).resolve().parent
    else:
        base_dir = BASE_DIR
    return str(base_dir / "school_data.db")

DB_PATH = get_db_path()

SCHOOL_INFO_COLUMNS = (
    "nom_annee",
    "nom_ecole",
    "nom_enseignant",
    "nom_inspecteur",
    "grade",
    "nom_assistant_pedagogique",
    "diplomes_scientifiques",
    "date_recrutement",
    "date_titularisation",
    "date_derniere_visite",
    "note_pedagogique",
    "resume_rapport_visite",
    "langue",
    "telephone",
    "email",
    "situation_familiale",
    "nombre_enfants",
)

ACTIVITIES_BY_MAJAL = {
    "اللغة العربية": ["تواصل شفوي", "قراءة", "إنتاج كتابي", "استعمال قواعد اللغة",
                      "محفوظات", "وحدة اللغة العربية", "رسم وإملاء", "إملاء", "رسم", "مطالعة", "حوار منظم"],
    "العلوم والتكنولوجيا": ["رياضيات", "إيقاظ علمي", "تربية تكنولوجية"],
    "التنشئة الفنية": ["تربية تشكيلية", "تربية موسيقية"],
    "التنشئة الاجتماعية": ["تربية إسلامية", "هدي قرآني", "عقيدة", "أخلاق", "عبادات", "تاريخ", "جغرافيا", "تربية مدنية"],
    "التنشئة البدنية": ["رياضة"],
}
DETAIL_KEYS_BY_NAMAT = {
    "منهجي": ["المحتوى", "هدف الحصة", "السند الرئيسي", "مفهوم صحي أو سكاني", "المهارة الحياتية", "القصيدة"],
    "إدماج": ["الكفايات المستهدفة", "مفهوم صحي أو سكاني", "المهارة الحياتية"],
    "تقييم": ["الأداء المنتظر", "مفهوم صحي أو سكاني", "المهارة الحياتية"],
    "دعم وعلاج": ["المعايير المستهدفة", "مفهوم صحي أو سكاني", "المهارة الحياتية"],
}

PLANS_NAMES = {
    "وحدة اللغة العربية": {"المجال":"اللغة العربية", "الأنشطة":["تواصل شفوي", "قراءة", "إنتاج كتابي", "استعمال قواعد اللغة",
                      "محفوظات", "وحدة اللغة العربية", "رسم وإملاء", "إملاء", "رسم", "مطالعة", "حوار منظم"], "الرمز":"لغة ع"},
    "رياضيات": {"المجال":"العلوم والتكنولوجيا", "الأنشطة":["رياضيات"], "الرمز":"رياضيات"},
    "إيقاظ علمي": {"المجال":"العلوم والتكنولوجيا", "الأنشطة":["إيقاظ علمي"], "الرمز":"إ. علمي"},
    "تربية تكنولوجية": {"المجال":"العلوم والتكنولوجيا", "الأنشطة":["تربية تكنولوجية"], "الرمز":"تر تك"},
    "تربية تشكيلية": {"المجال":"التنشئة الفنية", "الأنشطة":["تربية تشكيلية"], "الرمز":"تر تشك"},
    "تربية موسيقية": {"المجال":"التنشئة الفنية", "الأنشطة":["تربية موسيقية"], "الرمز":"تر موسيـ"},
    "تربية إسلامية": {"المجال":"التنشئة الاجتماعية", "الأنشطة":["تربية إسلامية", "هدي قرآني", "عقيدة", "أخلاق", "عبادات"], "الرمز":"تر إسلا"},
    "تاريخ": {"المجال":"التنشئة الاجتماعية", "الأنشطة":["تاريخ"], "الرمز":"تاريخ"},
    "جغرافيا": {"المجال":"التنشئة الاجتماعية", "الأنشطة":["جغرافيا"], "الرمز":"جغرافيا"},
    "تربية مدنية": {"المجال":"التنشئة الاجتماعية", "الأنشطة":["تربية مدنية"], "الرمز":"تر مدن"},
    "مواد الاجتماعية": {"المجال":"التنشئة الاجتماعية", "الأنشطة":["مواد الاجتماعية"], "الرمز":"مواد اجتماع"},
    "تنشئة فنية": {"المجال":"التنشئة الفنية", "الأنشطة":["تنشئة فنية", "تربية تشكيلية", "تربية موسيقية"]},
    "رياضة": {"المجال":"التنشئة البدنية", "الأنشطة":["رياضة"], "الرمز":"رياضة"},
}

LEVEL_FIRAKS = {
    "س1+2":{"الدرجة":"الأولى",
            "المستويات":["السنة الأولى", "السنة الثانية"],
            "الأقسام":[
                {"القسم":"س1","المستوى":"السنة الأولى"},
                {"القسم":"س2","المستوى":"السنة الثانية"}
            ]
        },
    "س3+4":{"الدرجة":"الثانية",
            "المستويات":["السنة الثالثة", "السنة الرابعة"], 
            "الأقسام":[
                {"القسم":"س3","المستوى":"السنة الثالثة"},
                {"القسم":"س4","المستوى":"السنة الرابعة"}
            ]
        },
    "س5+6":{"الدرجة":"الثالثة",
            "المستويات":["السنة الخامسة", "السنة السادسة"], 
            "الأقسام":[
                {"القسم":"س5","المستوى":"السنة الخامسة"},
                {"القسم":"س6","المستوى":"السنة السادسة"}
            ]
        }
}


def level_firak_label(firak_key):
    """Return the combined levels followed by the multi-grade label."""
    firak = LEVEL_FIRAKS.get(str(firak_key or "").strip())
    if firak is None:
        raise ValueError(f"فوج الأقسام المتعددة غير مسجل: {firak_key}")
    levels = list(dict.fromkeys(
        str(section["المستوى"]).strip()
        for section in firak["الأقسام"]
        if str(section.get("المستوى") or "").strip()
    ))
    if len(levels) < 2:
        raise ValueError(
            f"يجب أن يضم الفوج مستويين دراسيين على الأقل: {firak_key}"
        )
    return f"{' + '.join(levels)} فرق"


def level_firak_selection_label(firak_key):
    """Return a concise label for a multi-grade level selection."""
    firak = LEVEL_FIRAKS.get(str(firak_key or "").strip())
    if firak is None:
        raise ValueError(f"فوج الأقسام المتعددة غير مسجل: {firak_key}")
    levels = list(dict.fromkeys(
        str(section["المستوى"]).strip()
        for section in firak["الأقسام"]
        if str(section.get("المستوى") or "").strip()
    ))
    if len(levels) < 2:
        raise ValueError(
            f"يجب أن يضم الفوج مستويين دراسيين على الأقل: {firak_key}"
        )
    return " - ".join(levels)


def set_dialog_button_action_types(button_box):
    action_labels = (
        (("إغلاق", "اغلاق", "close"), "close"),
        (("تراجع", "رجوع", "عودة", "back"), "back"),
        (("إلغاء", "الغاء", "cancel"), "cancel"),
        (("حذف", "delete"), "delete"),
        (("مسح", "clear"), "clear"),
        (("تصدير", "export"), "export"),
        (("طباعة", "print"), "print"),
        (("معاينة", "استعراض", "عرض", "فتح", "browse", "open", "show"), "browse"),
        (("نسخ", "لصق", "copy", "paste"), "copy_paste"),
        (("تعديل", "edit"), "edit"),
        (("إضافة الحصص",), "save"),
        (("إضافة", "اضافة", "add"), "add"),
        (("حفظ", "موافق", "نعم", "متابعة الاستيراد", "save"), "save"),
    )
    for button in button_box.buttons():
        if button.property("actionType"):
            continue
        text = button.text().replace("&", "").strip().casefold()
        for labels, action_type in action_labels:
            if any(label.casefold() in text for label in labels):
                button.setProperty("actionType", action_type)
                break
        if button.property("actionType"):
            continue

        role_getter = getattr(button_box, "buttonRole", None)
        if not callable(role_getter):
            continue
        role = role_getter(button)
        role_name = getattr(role, "name", "")
        role_actions = {
            "AcceptRole": "save",
            "YesRole": "save",
            "RejectRole": "cancel",
            "NoRole": "cancel",
            "DestructiveRole": "delete",
            "HelpRole": "browse",
            "ApplyRole": "save",
        }
        action_type = role_actions.get(role_name)
        if action_type:
            button.setProperty("actionType", action_type)


def load_custom_font():
    if FONTS_DIR.exists() and FONTS_DIR.is_dir():
        font_files = list(FONTS_DIR.glob("*.ttf")) + list(FONTS_DIR.glob("*.otf"))
        if font_files:
            font_path = str(font_files[0])
            font_id = QFontDatabase.addApplicationFont(font_path)
            if font_id != -1:
                font_families = QFontDatabase.applicationFontFamilies(font_id)
                if font_families:
                    return font_families[0]
    return "Noto Naskh Arabic"


class ArabicDialogTranslator(QTranslator):
    BUTTON_TRANSLATIONS = {
        "&Yes": "&نعم",
        "Yes": "نعم",
        "&No": "&لا",
        "No": "لا",
        "&OK": "&حسناً",
        "OK": "حسناً",
        "&Cancel": "&إلغاء",
        "Cancel": "إلغاء",
        "&Close": "&إغلاق",
        "Close": "إغلاق",
        "&Save": "&حفظ",
        "Save": "حفظ",
        "Save All": "حفظ الكل",
        "Don't Save": "عدم الحفظ",
        "&Open": "&فتح",
        "Open": "فتح",
        "&Apply": "&تطبيق",
        "Apply": "تطبيق",
        "&Discard": "&تجاهل",
        "Discard": "تجاهل",
        "&Retry": "&إعادة المحاولة",
        "Retry": "إعادة المحاولة",
        "&Abort": "&إيقاف",
        "Abort": "إيقاف",
        "&Ignore": "&تجاهل",
        "Ignore": "تجاهل",
        "Yes to All": "نعم للكل",
        "No to All": "لا للكل",
        "Restore Defaults": "استعادة الافتراضي",
        "&Help": "&مساعدة",
        "Help": "مساعدة",
        "&Reset": "&إعادة التعيين",
        "Reset": "إعادة التعيين",
    }

    def translate(self, context, source_text, disambiguation=None, n=-1):
        return self.BUTTON_TRANSLATIONS.get(source_text, source_text)
