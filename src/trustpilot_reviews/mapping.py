"""Shape scraped reviews the way the caller's `Sort Trustpilot Reviews` node already reads Apify items.

That node looks for: rating|stars, text|reviewBody|body, title, publishedDate|experienceDate|date,
country, reviewId, companyUrl|businessUrl, companyDomain|companyWebsite, companyName|businessName,
companyTrustScore|companyStars, companyTotalReviews. Keep those names stable.
"""

from .scraper import BusinessUnit, Review, ScrapeResult

TRUSTPILOT_REVIEW_BASE = "https://www.trustpilot.com/review/"
TRUSTPILOT_SINGLE_REVIEW_BASE = "https://www.trustpilot.com/reviews/"


def company_fields(bu: BusinessUnit, domain: str) -> dict:
    slug = bu.identifying_name or domain
    return {
        "companyUrl": TRUSTPILOT_REVIEW_BASE + slug,
        "companyDomain": slug,
        "companyWebsite": bu.website_url,
        "companyName": bu.display_name,
        "companyTrustScore": bu.trust_score,
        "companyStars": bu.stars,
        "companyTotalReviews": bu.number_of_reviews,
        "companyReviewsLast12Months": bu.number_of_reviews_last_12_months,
        "companyCountry": bu.country_code,
        "companyIsClosed": bu.is_closed,
        "companyIsClaimed": bu.is_claimed,
    }


def review_item(r: Review, company: dict) -> dict:
    return {
        "reviewId": r.id,
        "reviewUrl": TRUSTPILOT_SINGLE_REVIEW_BASE + r.id if r.id else "",
        "rating": r.rating,
        "title": r.title,
        "text": r.text,
        "language": r.language,
        "publishedDate": r.published_date,
        "experienceDate": r.experienced_date,
        "updatedDate": r.updated_date,
        "reviewerName": r.consumer_name,
        "country": r.consumer_country,
        "reviewerReviewCount": r.consumer_review_count,
        "verified": r.verified,
        "verificationSource": r.verification_source,
        "likes": r.likes,
        "replyMessage": r.reply_message,
        "replyDate": r.reply_date,
        **company,
    }


def to_items(result: ScrapeResult, include_company_info: bool = True) -> list[dict]:
    company = company_fields(result.business_unit, result.domain) if include_company_info else {}
    return [review_item(r, company) for r in result.reviews]
