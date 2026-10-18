"""Every route the application serves, and nothing else.

The privacy suite crawls exactly these routers, so a route served from anywhere
else would escape it; ``hearth.app`` serves only what is listed here.
"""

from hearth.web.routes import admin, auth, health, pages, sharing

ROUTERS = (pages.router, auth.router, sharing.router, admin.router, health.router)
