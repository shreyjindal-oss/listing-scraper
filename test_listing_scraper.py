#!/usr/bin/env python3
"""Offline tests for listing_scraper.py using fixtures that mirror the
real page structures (verified against live Airbnb/Booking pages 2026-07-07)."""

import json
import listing_scraper as ls

# ---------------- Airbnb fixture (mirrors data-deferred-state-0) -----------

airbnb_state = {
    "niobeClientData": [[
        "StaysPdpSections:{...}",
        {"data": {"presentation": {"stayProductDetailPage": {"sections": {
            "sections": [
                {"sectionComponentType": "TITLE_DEFAULT", "section": {"__typename": "PdpTitleSection"}},
                {"sectionComponentType": "PHOTO_TOUR_SCROLLABLE", "section": {
                    "mediaItems": [
                        {"baseUrl": "https://a0.muscache.com/im/pictures/hosting/H1/original/p1.jpeg",
                         "accessibilityLabel": "Living room"},
                        {"baseUrl": "https://a0.muscache.com/im/pictures/hosting/H1/original/p2.jpeg",
                         "accessibilityLabel": "Bedroom 1"},
                    ]}},
                {"sectionComponentType": "DESCRIPTION_DEFAULT", "section": {
                    "htmlDescription": {"htmlText": "✨ COZY DESIGNER STAY WITH PRIVATE SUNDECK ✨<br /><br />🏡 Located in the peaceful Heartbeat City, Sector 107, Noida. Thoughtfully designed minimalist interiors."}}},
                {"sectionComponentType": "SLEEPING_ARRANGEMENT_IMAGES", "section": {
                    "arrangementDetails": [
                        {"title": "Bedroom 1", "subtitle": "1 double bed"},
                        {"title": "Bedroom 2", "subtitle": "1 double bed"}]}},
                {"sectionComponentType": "AMENITIES_DEFAULT", "section": {
                    "seeAllAmenitiesGroups": [
                        {"title": "Bathroom", "amenities": [
                            {"title": "Bath", "available": True},
                            {"title": "Shampoo", "available": True}]},
                        {"title": "Not included", "amenities": [
                            {"title": "Carbon monoxide alarm", "available": False}]}]}},
                {"sectionComponentType": "LOCATION_PDP", "section": {
                    "lat": 28.5493, "lng": 77.3781, "subtitle": "Noida, Uttar Pradesh, India"}},
                {"sectionComponentType": "MEET_YOUR_HOST", "section": {
                    "cardData": {"name": "Priyanshu", "isSuperhost": False, "userId": "12345"},
                    "hostDetails": ["Response rate: 100%", "Responds within an hour"]}},
                {"sectionComponentType": "POLICIES_DEFAULT", "section": {
                    "houseRules": [
                        {"title": "Check-in after 2:00 pm"},
                        {"title": "Checkout before 11:00 am"},
                        {"title": "8 guests maximum"}],
                    "houseRulesSections": [
                        {"title": "Checking in and out", "items": [
                            {"title": "Check-in after 2:00 pm"},
                            {"title": "Checkout before 11:00 am"}]},
                        {"title": "During your stay", "items": [
                            {"title": "8 guests maximum"}, {"title": "Pets allowed"}]}],
                    "previewSafetyAndProperties": [
                        {"title": "Carbon monoxide alarm not reported"},
                        {"title": "Exterior security cameras on property"},
                        {"title": "Smoke alarm"}],
                    "cancellationPolicyForDisplay": None}},
                {"sectionComponentType": "BOOK_IT_SIDEBAR", "section": {
                    "structuredDisplayPrice": None,
                    "personCapacity": 8}},
            ]}}}}}
    ]]
}

AIRBNB_HTML = f"""<!DOCTYPE html><html><head>
<meta property="og:title" content="Rental unit in Noida · ★5.0 · 2 bedrooms · 2 beds · 2 private bathrooms"/>
<meta property="og:description" content="Sundeck | Cozy Designer stay in Central Noida."/>
<meta name="description" content="Entire rental unit · COZY DESIGNER STAY"/>
<title>Sundeck | Cozy Designer stay in Central Noida. - Airbnb</title>
<script type="application/ld+json">{json.dumps({
    "@type": "VacationRental",
    "aggregateRating": {"ratingValue": 5.0, "reviewCount": 5},
    "address": {"addressLocality": "Noida", "addressCountry": "India"}})}</script>
</head><body>
<h1>Sundeck | Cozy Designer stay in Central Noida.</h1>
<script id="data-deferred-state-0" type="application/json">{json.dumps(airbnb_state)}</script>
{'x' * 6000}
</body></html>"""

# ---------------- Booking fixture ------------------------------------------
# Mirrors the raw SSR page: hotelPhotos JS gallery (single-quoted), room rows
# with data-name-en facility spans, a partner-offer row without an occupancy
# cell, full description block, and check-in/out policy text.

BOOKING_HTML = f"""<!DOCTYPE html><html><head>
<meta property="og:title" content="Queens Park by Viridian Apartments, London"/>
<script type="application/ld+json">{json.dumps({
    "@type": "Hotel", "@context": "http://schema.org",
    "name": "Queens Park by Viridian Apartments",
    "description": "Comfortable Accommodation: Queens Park by Viridian Apartments in London offers apartment-style living with free WiFi, 3.",
    "address": {"@type": "PostalAddress",
                "streetAddress": "70 Salusbury Road, Brent, London, NW6 6RN, United Kingdom",
                "addressLocality": "London", "addressRegion": "England",
                "postalCode": "NW6 6RN", "addressCountry": "GB"},
    "aggregateRating": {"@type": "AggregateRating", "ratingValue": 8.1, "reviewCount": 134},
    "priceRange": "₹ 75,000 - ₹ 99,000"})}</script>
<script>var x = {{'b_hotel_id': '634466'}};</script>
<script>
hotelPhotos: [
{{
id: '296210765',
thumb_url: 'https://cf.bstatic.com/xdata/images/hotel/max500/296210765.jpg?k=abc',
large_url: 'https://cf.bstatic.com/xdata/images/hotel/max1024x768/296210765.jpg?k=abc',
alt: "a kitchen and living room with black cabinets at Queens Park"
,highres_url: 'https://cf.bstatic.com/xdata/images/hotel/max1280x900/296210765.jpg?k=abc'
}},
{{
id: '609309216',
thumb_url: 'https://cf.bstatic.com/xdata/images/hotel/max500/609309216.jpg?k=def',
large_url: 'https://cf.bstatic.com/xdata/images/hotel/max1024x768/609309216.jpg?k=def',
alt: "a living room with a couch at Queens Park"
}}
]
</script>
</head><body>
<div data-atlas-latlng="51.5367048,-0.2064808"></div>
<div data-testid="property-most-popular-facilities-wrapper">
<ul><li>Non-smoking rooms</li><li>Free WiFi</li><li>Heating</li>
<li>Non-smoking rooms</li><li>Free WiFi</li><li>Heating</li></ul></div>
<div data-testid="property-description">Comfortable Accommodation: Queens Park by Viridian Apartments in London offers
apartment-style living with free WiFi, 3.2 km from Portobello Road Market. Each unit comes with a full
kitchen, washing machine and dedicated workspace, ideal for longer stays in the Queens Park area.</div>
<table id="hprt-table"><tbody>
<tr>
 <td><a class="hprt-roomtype-icon-link">One-Bedroom Apartment</a>
   <div class="hprt-roomtype-bed">1 double bed</div>
   <div class="hprt-facilities-facility">46 m²</div>
   <div class="hprt-facilities-facility" data-name-en="Private kitchen">Private kitchen</div>
   <ul class="hprt-facilities-others" aria-label="Included facilities">
     <li><span class="hprt-facilities-facility" data-name-en="Towels"><svg></svg>Towels</span></li>
     <li><span class="hprt-facilities-facility" data-name-en="Linen"><svg></svg>Linen</span></li>
     <li><span class="hprt-facilities-facility" data-name-en="Streaming service (like Netflix)"><svg></svg>Streaming service (like Netflix)</span></li>
   </ul></td>
 <td class="hprt-table-cell-occupancy">Max persons: 2</td>
 <td><div class="prco-valign-middle-helper">₹ 80,985</div></td>
 <td><ul class="hprt-conditions"><li>Free cancellation before 12 July</li></ul></td>
</tr>
<tr>
 <td class="hprt-table-cell-occupancy">Max persons: 2</td>
 <td><div class="prco-valign-middle-helper">₹ 88,032</div></td>
</tr>
<tr>
 <td><span class="bicon bicon-occupancy"></span><span class="bui-u-sr-only">Max persons: 3</span></td>
 <td><div class="prco-valign-middle-helper">₹ 78,106</div></td>
 <td><ul class="hprt-conditions"><li>No modifications</li></ul></td>
</tr>
<tr>
 <td><a class="hprt-roomtype-icon-link">One-Bedroom Apartment</a>
   <div class="hprt-roomtype-bed">1 double bed</div></td>
 <td class="hprt-table-cell-occupancy">Max persons: 2</td>
 <td><div class="prco-valign-middle-helper">₹ 95,686</div></td>
</tr>
</tbody></table>
<div data-testid="review-subscore">Cleanliness 8.3</div>
<div data-testid="review-subscore">Location 8.9</div>
<div data-testid="featuredreview-pros-cons">Great location, very clean apartment. The kitchen was well equipped.</div>
<div data-testid="property-section--content">Check-in From 16:00 You'll need to let the property
know in advance what time you'll arrive. Check-out Until 10:00 Cancellation and prepayment policies vary.</div>
<img src="https://cf.bstatic.com/xdata/images/hotel/max500/1234.jpg?k=abc" alt="Apartment"/>
<img src="https://cf.bstatic.com/xdata/images/hotel/max500/296210765.jpg?k=abc" alt="duplicate of gallery photo"/>
{'x' * 6000}
</body></html>"""


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    return cond


def main():
    ok = True
    a = ls.AirbnbParser().parse(
        AIRBNB_HTML,
        "https://www.airbnb.co.in/rooms/1711065697212792303?check_in=2026-07-10&check_out=2026-07-12")
    ok &= check("airbnb title", a["title"] and "Sundeck" in a["title"])
    ok &= check("airbnb id", a["listing_id"] == "1711065697212792303")
    ok &= check("airbnb type", a["property_type"] == "Rental unit")
    ok &= check("airbnb guests", a["capacity"]["guests"] == 8)
    ok &= check("airbnb bedrooms", a["capacity"]["bedrooms"] == 2)
    ok &= check("airbnb photos", len(a["photos"]) == 2)
    ok &= check("airbnb amenities", len(a["amenities"]) == 2
                and a["amenities"][1]["items"][0]["available"] is False)
    ok &= check("airbnb latlng", a["location"]["lat"] == 28.5493)
    ok &= check("airbnb host", a["host"]["name"] == "Priyanshu"
                and a["host"]["response_rate"] == "100%")
    ok &= check("airbnb rules", a["house_rules"]["check_in"] == "Check-in after 2:00 pm"
                and "Pets allowed" in a["house_rules"]["other"])
    ok &= check("airbnb safety", len(a["safety_and_property"]) == 3)
    ok &= check("airbnb rating", a["reviews"]["rating"] == 5.0 and a["reviews"]["count"] == 5)
    ok &= check("airbnb sleeping", len(a["sleeping_arrangement"]) == 2)
    ok &= check("airbnb price note", a["pricing"]["note"] is not None
                and a["pricing"]["check_in"] == "2026-07-10")
    ok &= check("airbnb description", "COZY DESIGNER" in (a["description"] or "")
                and "<br" not in (a["description"] or ""))

    b = ls.BookingParser().parse(
        BOOKING_HTML,
        "https://www.booking.com/hotel/gb/queens-park-apartments-by-flying-butler.en-gb.html?checkin=2026-07-13&checkout=2026-07-17&group_adults=2")
    ok &= check("booking title", b["title"] == "Queens Park by Viridian Apartments")
    ok &= check("booking id", b["listing_id"] == "634466")
    ok &= check("booking type", b["property_type"] == "Hotel")
    ok &= check("booking address", "Salusbury" in b["location"]["address"])
    ok &= check("booking city", b["location"]["city"] == "London")
    ok &= check("booking latlng", b["location"]["lat"] == 51.5367048)
    ok &= check("booking description full",
                "washing machine and dedicated workspace" in (b["description"] or ""))
    ok &= check("booking rooms", len(b["rooms"]) == 2
                and len(b["rooms"][0]["options"]) == 3
                and b["rooms"][0]["options"][0]["price"] == "₹ 80,985")
    ok &= check("booking occupancy", b["rooms"][0]["options"][0]["occupancy"] == "Max persons: 2")
    ok &= check("booking occupancy fallback",
                b["rooms"][0]["options"][2]["occupancy"] == "Max persons: 3"
                and b["rooms"][0]["options"][2]["price"] == "₹ 78,106")
    ok &= check("booking beds", b["rooms"][0]["beds"] == "1 double bed")
    ok &= check("booking room facilities",
                "Towels" in b["rooms"][0]["facilities"]
                and "46 m²" in b["rooms"][0]["facilities"])
    ok &= check("booking size", b["rooms"][0]["size"] == "46 m²")
    ok &= check("booking facilities dedup",
                len(b["amenities"][0]["items"]) == 3)
    room_amen = next((g for g in b["amenities"] if g["group"] == "Room amenities"), None)
    ok &= check("booking room amenities group",
                room_amen is not None
                and any(i["title"] == "Towels" for i in room_amen["items"])
                and any("Netflix" in i["title"] for i in room_amen["items"]))
    ok &= check("booking pricing", b["pricing"]["nights"] == 4
                and b["pricing"]["cheapest_total_for_stay"] == 78106.0
                and abs(b["pricing"]["cheapest_per_night"] - 19526.5) < 0.01)
    ok &= check("booking reviews", b["reviews"]["rating"] == 8.1
                and b["reviews"]["count"] == 134
                and b["reviews"]["category_ratings"]["cleanliness"] == 8.3)
    ok &= check("booking review items",
                any("Great location" in (i["text"] or "") for i in b["reviews"]["items"]))
    photo_ids = [ls.BookingParser._photo_id(p["url"]) for p in b["photos"]]
    ok &= check("booking photos gallery",
                len(b["photos"]) == 3                       # 2 gallery + 1 unique img
                and photo_ids.count("296210765") == 1       # deduped across variants
                and "kitchen" in (b["photos"][0]["caption"] or ""))
    ok &= check("booking rules", b["house_rules"]["check_in"] == "From 16:00"
                and b["house_rules"]["check_out"] == "Until 10:00")

    # URL detection
    ok &= check("detect airbnb", ls.detect_platform("https://www.airbnb.co.in/rooms/1") == "airbnb")
    ok &= check("detect booking", ls.detect_platform("https://www.booking.com/hotel/gb/x.html") == "booking")
    try:
        ls.detect_platform("https://example.com")
        ok &= check("detect unsupported", False)
    except ls.ScraperError as e:
        ok &= check("detect unsupported", e.code == "UNSUPPORTED_URL")

    print("\nALL TESTS PASSED" if ok else "\nSOME TESTS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
