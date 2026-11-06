"""Rulebook obligation -> draft engine filing type.

Lets any calendar row, meeting or alert open the Draft wizard on the right
document: /draft?company=<symbol or name>&type=<filing type id>.
"""
from urllib.parse import urlencode

DRAFT_TYPE_FOR_RULE = {
    "IN-MCA-AGM": "agm_notice",
    "IN-MCA-DIR12": "dir12_covering",
    "IN-MCA-MR1": "mr1_kmp",
    "IN-MCA-MGT14": "mgt14_resolution",
    "IN-MCA-PAS3": "pas3_covering",
    "IN-MCA-CHG1": "chg1_covering",
    "IN-MCA-CHG4": "chg1_covering",
    "IN-MCA-INC20A": "inc20a_covering",
    "IN-MCA-ADT1": "adt1_covering",
    "IN-MCA-ADT1-EVT": "adt1_covering",
    "IN-MCA-DPT3": "dpt3_covering",
    "IN-MCA-MSME1": "msme1_covering",
    "IN-MCA-BEN2": "ben2_covering",
    "IN-MCA-CSR2": "csr_resolution",
    "IN-MCA-MR3": "secretarial_audit_mr3",
    "IN-MCA-MINUTES": "board_minutes",
    "IN-MCA-BOARD": "board_notice",
    "IN-MCA-BOARD-HALF": "board_notice",
    "IN-FEMA-FCGPR": "fcgpr_covering",
    "IN-FEMA-FLA": "fla_return",
    "IN-SEBI-REG33": "reg33_results",
    "IN-SEBI-REG31": "shareholding_pattern",
    "IN-SEBI-REG27": "integrated_governance",
    "IN-SEBI-REG13": "reg13_grievance",
    "IN-SEBI-REG74": "depositories_certificate",
    "IN-SEBI-REG76": "reg74_recon",
    "IN-SEBI-REG23": "rpt_disclosure",
    "IN-SEBI-REG24A": "reg24a_ascr",
    "IN-SEBI-REG34": "reg34_ar",
    "IN-SEBI-BRSR": "brsr",
    "IN-SEBI-SAST31-4": "sast_31",
    "IN-SEBI-PIT-TW": "trading_window_closure",
    "IN-SEBI-REG30": "reg30_disclosure",
    "IN-SEBI-REG30-BM": "outcome_board_meeting",
    "IN-SEBI-REG32": "reg32_deviation",
    "IN-SEBI-REG47": "reg47_newspaper",
    "IN-MCA-MBP1": "mbp1_disclosure",
    "IN-SEBI-REG29": "reg29_board_meeting",
    "IN-SEBI-REG44": "voting_results",
}


def draft_url(rule_id, company, display_name=""):
    """URL of the Draft wizard preset for this obligation, or None."""
    ftype = DRAFT_TYPE_FOR_RULE.get(rule_id)
    if not ftype or not company:
        return None
    return "/draft?" + urlencode({"company": company, "name": display_name or company, "type": ftype})
