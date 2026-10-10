"""GET-only public market data. Never calls any account or order endpoint."""
from __future__ import annotations

import re
from datetime import timedelta

import requests

from .common import APP, digest, env_values, external_reference, now, parse_time, stamp, write


class Collector:
    def __init__(self, directory, keys=None):
        self.directory = directory
        self.keys = keys if keys is not None else env_values()
        if not self.keys["APCA_API_KEY_ID"] or not self.keys["APCA_API_SECRET_KEY"]:
            raise ValueError("Missing Alpaca data credentials")
        self.records = []
        self.errors = []

    def get(self, name, path, params, fmp=False):
        domain = "https://financialmodelingprep.com" if fmp else "https://data.alpaca.markets"
        if fmp and not self.keys["FMP_API_KEY"]:
            self.errors.append({"source": name, "status": "unknown", "reason": "No FMP key"})
            return None
        query = dict(params)
        headers = {}
        if fmp:
            query["apikey"] = self.keys["FMP_API_KEY"]
        else:
            headers = {"APCA-API-KEY-ID": self.keys["APCA_API_KEY_ID"], "APCA-API-SECRET-KEY": self.keys["APCA_API_SECRET_KEY"]}
        try:
            response = requests.get(domain + path, params=query, headers=headers, timeout=25)
            if response.status_code != 200:
                self.errors.append({"source": name, "status": "unknown", "reason": f"HTTP {response.status_code}"})
                return None
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            # Exception strings may contain API keys in URLs: store only the class.
            self.errors.append({"source": name, "status": "unknown", "reason": type(exc).__name__})
            return None
        file = self.directory / "raw" / (name + ".json")
        write(file, payload, exclusive=True)
        record = {"id": name, "file": str(file.relative_to(self.directory)), "source": domain + path,
                  "query": params, "fetched_at": stamp(), "sha256": digest(file), "classification": "observed"}
        self.records.append(record)
        return payload


def collect(directory, config, keys=None):
    collector = Collector(directory, keys=keys)
    started = now()
    # Only completed prior UTC days; never treat today's partial daily bar as a full day.
    end = started.replace(hour=0, minute=0, second=0, microsecond=0)
    begin = end - timedelta(days=200)
    symbols = config["universe"] + config["benchmarks"]
    bars = {}
    token = None
    seen_tokens = set()
    for page in range(30):
        params = {"symbols": ",".join(symbols), "timeframe": "1Day", "start": begin.isoformat(),
                  "end": end.isoformat(), "limit": 10000, "adjustment": "split", "feed": config["quote_feed"]}
        if token:
            params["page_token"] = token
        response = collector.get(f"bars_{page}", "/v2/stocks/bars", params)
        if not isinstance(response, dict) or not isinstance(response.get("bars"), dict):
            collector.errors.append({"source": "bars", "status": "unknown", "reason": "Missing daily bars"})
            break
        for symbol, rows in response["bars"].items():
            bars.setdefault(symbol, []).extend(rows)
        token = response.get("next_page_token")
        if not token:
            break
        if token in seen_tokens:
            raise ValueError("Daily bar pagination did not advance")
        seen_tokens.add(token)
    else:
        raise ValueError("Daily bar page limit exceeded")
    quote_response = collector.get("quotes", "/v2/stocks/quotes/latest", {"symbols": ",".join(symbols), "feed": config["quote_feed"]})
    quotes = quote_response.get("quotes", {}) if isinstance(quote_response, dict) else {}
    evidence = []
    features = []
    for symbol in symbols:
        rows = sorted({r["t"]: r for r in bars.get(symbol, []) if parse_time(r["t"]) < end}.values(), key=lambda r: r["t"])
        closes = [float(r["c"]) for r in rows]
        item = {"symbol": symbol, "daily_rows": len(rows), "classification": "derived", "source_ids": ["price:" + symbol]}
        if closes and all(c > 0 for c in closes):
            item.update(last_close=closes[-1], last_bar=rows[-1]["t"],
                        return_5d=(closes[-1] / closes[-6] - 1) if len(closes) > 5 else None,
                        return_20d=(closes[-1] / closes[-21] - 1) if len(closes) > 20 else None,
                        return_60d=(closes[-1] / closes[-61] - 1) if len(closes) > 60 else None,
                        mean_20d=sum(closes[-20:]) / 20 if len(closes) >= 20 else None,
                        mean_60d=sum(closes[-60:]) / 60 if len(closes) >= 60 else None,
                        volume_feed=config["quote_feed"], volume_note="IEX is a single venue; not consolidated market volume")
            evidence.append({"id": "price:" + symbol, "symbol": symbol, "kind": "derived_price", "as_of": rows[-1]["t"], "data": dict(item)})
        else:
            item["status"] = "unknown"
        features.append(item)
        if symbol not in config["universe"]:
            continue
        article_response = collector.get("news_" + symbol, "/v1beta1/news", {
            "symbols": symbol, "start": (started - timedelta(days=config["news_days"])).isoformat(),
            "end": started.isoformat(), "limit": config["news_per_symbol"], "sort": "desc", "include_content": "false"})
        if isinstance(article_response, dict) and isinstance(article_response.get("news"), list):
            for article in article_response["news"]:
                # Updated stories are only available from their update, not original publication.
                published = article.get("updated_at") or article.get("created_at")
                if not published or parse_time(published) > started:
                    continue
                evidence.append({"id": f"news:{symbol}:{article['id']}", "symbol": symbol, "kind": "news",
                                 "as_of": published, "classification": "observed", "url": article.get("url"),
                                 "headline": article.get("headline"), "summary": re.sub("<[^>]+>", " ", article.get("summary") or "")[:1400],
                                 "publisher": article.get("source"), "note": "Reported claims; not independently verified"})
        financials = collector.get("income_" + symbol, "/stable/income-statement", {"symbol": symbol, "period": "quarter", "limit": 2}, fmp=True)
        if isinstance(financials, list):
            for index, row in enumerate(financials):
                accepted = row.get("acceptedDate") or row.get("filingDate")
                # Date-only acceptance is conservatively available the following UTC day.
                try:
                    available = parse_time(accepted.replace(" ", "T") + ("Z" if not re.search(r"(Z|[+-]\d\d:\d\d)$", accepted) else ""))
                    if len(accepted) == 10:
                        available += timedelta(days=1)
                except (ValueError, AttributeError, TypeError):
                    continue
                if available > started:
                    continue
                keys = ("date", "fiscalYear", "period", "revenue", "grossProfit", "operatingIncome", "netIncome", "eps", "reportedCurrency", "filingDate", "acceptedDate")
                evidence.append({"id": f"income:{symbol}:{index}", "symbol": symbol, "kind": "financials",
                                 "classification": "observed", "as_of": available.isoformat(), "data": {k: row.get(k) for k in keys},
                                 "note": "Current vendor vintage, suitable for this forward observation only"})
    frozen = stamp()
    for record in collector.records:
        payload = __import__("json").loads((directory / record["file"]).read_text(encoding="utf-8"))
        if isinstance(payload, list):
            record["rows"] = len(payload)
        elif record["id"].startswith("bars_"):
            record["rows"] = sum(len(rows) for rows in payload.get("bars", {}).values())
        elif record["id"].startswith("news_"):
            record["rows"] = len(payload.get("news", []))
        else:
            record["rows"] = len(payload.get("quotes", {}))
    packet = {"snapshot_id": directory.name, "truth_reference": external_reference(), "frozen_at": frozen,
              "observation_interval": {"start": begin.isoformat(), "end": frozen},
              "features": features, "evidence": evidence, "quotes": quotes, "errors": collector.errors,
              "records": collector.records,
              "source_code": {p.name: digest(p) for p in APP.glob("*.py")},
              "classification": {"market_data": "observed", "price_features": "derived", "portfolio_and_fills": "assumption", "actual_fees_slippage_impact": "unknown"}}
    write(directory / "packet.json", packet, exclusive=True)
    write(directory / "config.json", config, exclusive=True)
    write(directory / "manifest.json", {"snapshot_id": directory.name, "packet_sha256": digest(directory / "packet.json"),
          "config_sha256": digest(directory / "config.json"), "source_code": packet["source_code"], "input_files": collector.records}, exclusive=True)
    return packet
