from api.auth import admin_portfolio
from api.models import Committee, Portfolio, ProposalCategory, User, UserRole
from sqlalchemy import and_, or_


WELFARE_COMMITTEE_NAMES = {
    "Welfare Comm",
    "HeaRHtfelt",
    "Green Commm",
    "BakeRHs and Cooks",
    "Children",
    "Pioneers",
    "Special Projects",
    "Special Needs",
    "Welfare D",
}

# Social committees support the full proposal workflow. Welfare proposals are
# intentionally limited to the categories owned by that portfolio.
PORTFOLIO_CATEGORIES: dict[Portfolio, tuple[ProposalCategory, ...]] = {
    Portfolio.social: (
        ProposalCategory.event,
        ProposalCategory.initiative,
        ProposalCategory.welfare,
        ProposalCategory.decor,
        ProposalCategory.pantry_cleaning,
        ProposalCategory.merch,
        ProposalCategory.pubs,
    ),
    Portfolio.welfare: (
        ProposalCategory.event,
        ProposalCategory.initiative,
    ),
}


def committee_portfolio(committee: Committee) -> Portfolio:
    return committee.portfolio or (
        Portfolio.welfare if committee.name in WELFARE_COMMITTEE_NAMES else Portfolio.social
    )


def portfolio_categories(portfolio: Portfolio) -> tuple[ProposalCategory, ...]:
    return PORTFOLIO_CATEGORIES[portfolio]


def category_allowed_for_committee(committee: Committee, category: ProposalCategory) -> bool:
    return category in portfolio_categories(committee_portfolio(committee))


def admin_can_access_committee(user: User, committee: Committee | None) -> bool:
    if committee is None:  # a request filed under a free-text CCA has no committee to scope by
        return True
    return user.role != UserRole.admin or committee_portfolio(committee) == admin_portfolio(user)


def admin_committee_filter(user: User):
    """SQLAlchemy expression for committees visible to a portfolio admin."""
    target = admin_portfolio(user)
    if target == Portfolio.social:
        return or_(Committee.portfolio == target, and_(Committee.portfolio.is_(None), Committee.name.not_in(WELFARE_COMMITTEE_NAMES)))
    return or_(Committee.portfolio == target, and_(Committee.portfolio.is_(None), Committee.name.in_(WELFARE_COMMITTEE_NAMES)))


def sends_confirmation_email(committee: Committee, category: ProposalCategory) -> bool:
    return category == ProposalCategory.event or (
        category == ProposalCategory.initiative and committee_portfolio(committee) == Portfolio.welfare
    )
