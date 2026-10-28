"""Word lists for fictional organizations. Combinations are invented, not sourced."""

from __future__ import annotations

COMPANY_PREFIXES = (
    "Harborline", "Quillstone", "Brightwater", "Copperfield", "Ironvale", "Larkspur",
    "Meridian Ridge", "Northgate", "Oakhollow", "Pinecrest", "Redfern", "Saltmarsh",
    "Silverbirch", "Stonebridge", "Thornbury", "Westmere", "Amberly", "Bluecairn",
    "Cedarbrook", "Driftwood", "Fernhill", "Glenmoor", "Hollowell", "Juniper Bay",
    "Kestrel", "Lowfield", "Marrowby", "Netherby", "Orchard Lane", "Ravensworth",
    "Sablewood", "Tidewater", "Umberly", "Vantor", "Willowmere", "Yarrowdale",
)

INDUSTRIES: dict[str, tuple[str, ...]] = {
    "Logistics": ("Freight", "Logistics", "Distribution"),
    "Healthcare": ("Health Partners", "Clinical Group", "Care Network"),
    "Manufacturing": ("Fabrication", "Industries", "Components"),
    "Hospitality": ("Hospitality Group", "Restaurants", "Hotels"),
    "Professional Services": ("Advisory", "Consulting Group", "Associates"),
    "Construction": ("Builders", "Construction", "Contracting"),
    "Retail": ("Outfitters", "Markets", "Goods"),
    "Technology": ("Software", "Systems", "Labs"),
    "Education": ("Learning Center", "Academy", "Schools"),
    "Nonprofit": ("Foundation", "Community Services", "Alliance"),
}

LEGAL_SUFFIXES = ("LLC", "Inc.", "Co.", "Corp.", "LLP")

STATES = ("NY", "NJ", "CT", "PA", "MA", "IL", "CA", "TX", "FL", "GA", "NC", "OH")

# Fictional payroll vendors.
PAYROLL_PROVIDERS = (
    "Ledgerline Payroll", "Northwind HCM", "PayCrest", "Tallyhouse", "Brightledger",
    "In-house (custom export)",
)

SPONSOR_CONTACT_TITLES = (
    "HR Manager", "Payroll Manager", "Benefits Administrator", "Controller",
    "Director of People Operations", "Office Manager", "CFO", "Payroll Specialist",
)

RELATIONSHIP_MANAGERS = (
    "Dana Whitfield", "Marcus Oyelaran", "Priya Raman", "Tom Castellano", "Lena Hoffmann",
)

SERVICE_TIERS = ("core", "core", "core", "plus", "premier")
