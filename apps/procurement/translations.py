"""Hausa translations for the e-procurement platform.

Design requirement: "Hausa + English toggle from day one (not a translation patch):
all labels in a JSON dictionary, one screen tested end-to-end in Hausa before go-live."

This module provides the translation dictionary and helper functions.
"""

TRANSLATIONS = {
    "en": {
        # Navigation
        "nav_home": "Home",
        "nav_tenders": "Tenders",
        "nav_awards": "Awards",
        "nav_suppliers": "Suppliers",
        "nav_contracts": "Contracts",
        "nav_register": "Register",
        "nav_login": "Login",
        "nav_logout": "Logout",
        "nav_status": "System Status",
        "nav_help": "Help",
        
        # Common actions
        "action_search": "Search",
        "action_filter": "Filter",
        "action_submit": "Submit",
        "action_save": "Save",
        "action_cancel": "Cancel",
        "action_back": "Back",
        "action_next": "Next",
        "action_download": "Download",
        "action_print": "Print",
        "action_view_details": "View Details",
        "action_bid_now": "Bid Now",
        
        # Tender-related
        "tender_title": "Tender Title",
        "tender_reference": "Reference Number",
        "tender_agency": "Procuring Agency",
        "tender_category": "Category",
        "tender_method": "Procurement Method",
        "tender_value": "Estimated Value",
        "tender_closing_date": "Closing Date",
        "tender_published_date": "Published Date",
        "tender_status": "Status",
        "tender_description": "Description",
        "tender_documents": "Documents",
        "tender_requirements": "Requirements",
        
        # Status values
        "status_open": "Open",
        "status_closed": "Closed",
        "status_awarded": "Awarded",
        "status_cancelled": "Cancelled",
        "status_evaluating": "Under Evaluation",
        
        # Bid-related
        "bid_submit": "Submit Bid",
        "bid_amount": "Bid Amount",
        "bid_validity": "Bid Validity (days)",
        "bid_documents": "Bid Documents",
        "bid_receipt": "Bid Receipt",
        "bid_confirmation": "Your bid has been submitted successfully",
        "bid_receipt_number": "Receipt Number",
        "bid_commitment_hash": "Commitment Hash",
        "bid_submitted_at": "Submitted At",
        
        # Supplier registration
        "register_title": "Supplier Registration",
        "register_company_name": "Company Name",
        "register_rc_number": "RC Number",
        "register_tin": "Tax ID Number (TIN)",
        "register_address": "Address",
        "register_phone": "Phone Number",
        "register_email": "Email Address",
        "register_category": "Business Category",
        "register_experience": "Years of Experience",
        "register_step": "Step",
        "register_of": "of",
        
        # Awards
        "award_supplier": "Winning Supplier",
        "award_amount": "Award Amount",
        "award_date": "Award Date",
        "award_reason": "Reason for Award",
        
        # Messages
        "msg_no_results": "No results found",
        "msg_loading": "Loading...",
        "msg_error": "An error occurred",
        "msg_success": "Operation successful",
        "msg_required_field": "This field is required",
        "msg_invalid_email": "Please enter a valid email address",
        "msg_password_mismatch": "Passwords do not match",
        
        # Footer
        "footer_copyright": "© 2025 Taraba State Government",
        "footer_privacy": "Privacy Policy",
        "footer_terms": "Terms of Use",
        "footer_contact": "Contact Us",
        "footer_accessibility": "Accessibility",
        
        # Accessibility
        "a11y_skip_to_content": "Skip to main content",
        "a11y_language_selector": "Select language",
    },
    
    "ha": {
        # Navigation
        "nav_home": "Gida",
        "nav_tenders": "Tayin Saye",
        "nav_awards": "Kyaututtuka",
        "nav_suppliers": "Masu Samarwa",
        "nav_contracts": "Kwangiloli",
        "nav_register": "Yi Rajista",
        "nav_login": "Shiga",
        "nav_logout": "Fita",
        "nav_status": "Matsayin Tsarin",
        "nav_help": "Taimako",
        
        # Common actions
        "action_search": "Nema",
        "action_filter": "Tace",
        "action_submit": "Aika",
        "action_save": "Ajiye",
        "action_cancel": "Soke",
        "action_back": "Baya",
        "action_next": "Gaba",
        "action_download": "Saukarwa",
        "action_print": "Buga",
        "action_view_details": "Duba Cikakken Bayani",
        "action_bid_now": "Yi Tayin Yanzu",
        
        # Tender-related
        "tender_title": "Taken Tayin Saye",
        "tender_reference": "Lambar Magana",
        "tender_agency": "Hukumar Saye",
        "tender_category": "Nau'i",
        "tender_method": "Hanyar Saye",
        "tender_value": "Kiyasin Darajar",
        "tender_closing_date": "Ranar Rufewa",
        "tender_published_date": "Ranar Bugawa",
        "tender_status": "Matsayi",
        "tender_description": "Bayani",
        "tender_documents": "Takaddun",
        "tender_requirements": "Buƙatu",
        
        # Status values
        "status_open": "A Buɗe",
        "status_closed": "An Rufe",
        "status_awarded": "An Bayar",
        "status_cancelled": "An Soke",
        "status_evaluating": "Ana Kimantawa",
        
        # Bid-related
        "bid_submit": "Aika Tayi",
        "bid_amount": "Adadin Tayi",
        "bid_validity": "Lokacin Ingancin Tayi (kwanaki)",
        "bid_documents": "Takaddun Tayi",
        "bid_receipt": "Rasidin Tayi",
        "bid_confirmation": "An aika tayin ku cikin nasara",
        "bid_receipt_number": "Lambar Rasidi",
        "bid_commitment_hash": "Hashin Alƙawari",
        "bid_submitted_at": "Lokacin Aikawa",
        
        # Supplier registration
        "register_title": "Rajistar Mai Samarwa",
        "register_company_name": "Sunan Kamfani",
        "register_rc_number": "Lambar RC",
        "register_tin": "Lambar Haraji (TIN)",
        "register_address": "Adireshi",
        "register_phone": "Lambar Waya",
        "register_email": "Adireshin Imel",
        "register_category": "Nau'in Kasuwanci",
        "register_experience": "Shekarun Gogewa",
        "register_step": "Mataki",
        "register_of": "na",
        
        # Awards
        "award_supplier": "Mai Samarwa Mai Nasara",
        "award_amount": "Adadin Kyauta",
        "award_date": "Ranar Bayarwa",
        "award_reason": "Dalilin Bayarwa",
        
        # Messages
        "msg_no_results": "Ba a sami sakamako ba",
        "msg_loading": "Ana lodawa...",
        "msg_error": "An samu kuskure",
        "msg_success": "Aiki ya yi nasara",
        "msg_required_field": "Wannan filin yana da mahimmanci",
        "msg_invalid_email": "Da fatan za a shigar da ingantaccen adireshin imel",
        "msg_password_mismatch": "Kalmar sirri ba ta dace ba",
        
        # Footer
        "footer_copyright": "© 2025 Gwamnatin Jihar Taraba",
        "footer_privacy": "Manufar Sirri",
        "footer_terms": "Sharuɗɗan Amfani",
        "footer_contact": "Tuntuɓe Mu",
        "footer_accessibility": "Samun Dama",
        
        # Accessibility
        "a11y_skip_to_content": "Tsallake zuwa babban abun ciki",
        "a11y_language_selector": "Zaɓi harshe",
    }
}


def get_translation(key: str, language: str = "en") -> str:
    """Get translation for a key in the specified language.
    
    Falls back to English if translation not found.
    """
    if language not in TRANSLATIONS:
        language = "en"
    
    return TRANSLATIONS[language].get(key, TRANSLATIONS["en"].get(key, key))


def get_all_translations(language: str = "en") -> dict:
    """Get all translations for a language."""
    if language not in TRANSLATIONS:
        language = "en"
    return TRANSLATIONS[language].copy()


def validate_translations():
    """Validate that all English keys have Hausa translations."""
    en_keys = set(TRANSLATIONS["en"].keys())
    ha_keys = set(TRANSLATIONS["ha"].keys())
    
    missing_in_hausa = en_keys - ha_keys
    extra_in_hausa = ha_keys - en_keys
    
    if missing_in_hausa:
        raise ValueError(f"Missing Hausa translations for: {missing_in_hausa}")
    
    if extra_in_hausa:
        print(f"Warning: Extra Hausa keys not in English: {extra_in_hausa}")
    
    return True
