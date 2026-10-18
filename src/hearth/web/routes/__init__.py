"""Every route the application serves, and nothing else.

The privacy suite crawls exactly these routers, so a route served from anywhere
else would escape it; ``hearth.app`` serves only what is listed here.
"""

from hearth.web.routes import admin, auth, connect, health, pages, prices, review, sharing

ROUTERS = (
    pages.router,
    review.router,
    prices.router,
    auth.router,
    sharing.router,
    sharing.connections_router,
    connect.router,
    admin.router,
    health.router,
)
