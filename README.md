# GameSearch

Fast multi-source game search application with backend API and responsive frontend. Search across multiple repack sites from a single interface.

## Features

- **Multi-source search**: Query multiple repack sites simultaneously
- **Smart scoring**: Relevance-ranked results with canonical Steam name matching
- **Source toggling**: Enable/disable specific sources
- **Language support**: EN/FR/DE/ES interface
- **Responsive UI**: Modern, interactive interface with loading states
- **Search timing**: Real-time feedback on search duration

### Backend (FastAPI)
- **Parallel provider execution**: ThreadPoolExecutor with configurable time budget
- **Provider modules**: Dynamic loading of individual scrapers
- **Scoring system**: MIN_ACCEPT_SCORE=2000 threshold with deduplication
- **Steam integration**: Canonical name resolution for better query precision
- **Caching**: TTLCache for provider modules and results

### Frontend (Vanilla JS + TailwindCSS)
- **Settings modal**: Source toggles and language selector
- **LocalStorage**: Persistent user preferences
- **AbortController**: Prevents concurrent searches
- **Status indicators**: Search timing and loading states
- **Interactive elements**: Clickable featured images, smooth animations

### Supported Providers

#### Fast Providers
- **SteamRIP**: Primary source with comprehensive metadata
- **GOG-Games**: Official GOG catalog integration

#### Slow Providers (Optimized)
- **FitGirl**: FitGirl Repacks with timeout/sleep optimizations
- **AnkerGames**: Local index caching with reduced scraping
- **CPGRepacks**: WordPress-based scraper with pagination limits
- **CrackedGames**: HTML scraper with Cloudflare handling
- **GameBounty**: Next.js site with multi-strategy parsing
- **ByXatab**: DLE engine scraper with Russian/English support

## Quick Start

### Prerequisites
- Python 3.8+
- pip and virtualenv

### Installation

```bash
# Clone the repository
git clone https://github.com/blizzx4644/GameSearch.git
cd "GameSearch"

# Install dependencies
pip install -r requirements.txt
```

### Development Setup

```bash
# Start backend
cd backend
python -m uvicorn app:app --reload --host 0.0.0.0 --port 8000

```

Visit `http://localhost:8000` to use the application.



## 🔧 Configuration


### Provider Settings
- **Fast mode**: Only fast providers (SteamRIP, GOG-Games)
- **Slow mode**: All providers with optimized timeouts
- **Custom mode**: User-selected sources via settings

## API Endpoints

### Search
```
GET /api/search?q={query}&mode={mode}&sources={sources}&allow_broad_fallback={0|1}
```

Parameters:
- `q`: Search query
- `mode`: `fast`|`slow`|`all` (default: fast)
- `sources`: Comma-separated provider list (optional)
- `allow_broad_fallback`: Allow fallback to original query (default: 0)

Response:
```json
{
  "results": [...],
  "used_providers": [...],
  "provider_query": "...",
  "timing_ms": 1234
}
```

### Provider Management
```
GET /api/providers     # List available providers
GET /api/provider      # Alias for /api/providers
```

### Adding New Providers

Each provider should implement:
```python
def do_search(query: str, limit: int = 15, verbose: bool = True) -> list[dict]:
    """Return list of games with name, url, and metadata."""
    pass
```

Required fields per result:
- `name`: Game title
- `url`: Direct link to game page
- Optional: `image`, `size`, `version`, `date`, `genres`, etc.

##  Acknowledgments

- **FastAPI**: Modern Python web framework
- **BeautifulSoup**: HTML parsing library
- **TailwindCSS**: Utility-first CSS framework
- **CloudScraper**: Cloudflare bypass utility
- **Steam**: Game metadata and canonical names
