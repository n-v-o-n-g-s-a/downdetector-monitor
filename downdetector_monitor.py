#!/usr/bin/env python3
"""
Carrier Status Monitor for Cityside Fiber NOC
Actively polls Cogent, Lumen, and Microsoft status pages.
Logs incidents to Notion when outages are detected.
"""
 
import os
import logging
import time
from datetime import datetime
from typing import Dict, Optional
import requests
from bs4 import BeautifulSoup
 
# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
 
# Configuration
NOTION_API_KEY = os.getenv('NOTION_API_KEY', 'your_notion_api_key_here')
NOTION_DATABASE_ID = 'faf29e8b-81b9-495b-9c85-fb6746525f8d'
 
# Carriers to monitor
MONITORED_CARRIERS = {
    'Cogent': 'http://status.cogentco.com',
    'Lumen': 'https://lumen.statuspage.io/',
    'Microsoft': 'https://status.microsoft365.com/'
}
 
NOTION_API_ENDPOINT = 'https://api.notion.com/v1'
NOTION_API_VERSION = '2024-06-15'
 
# Poll configuration
POLL_INTERVAL = 60  # seconds between polls
FAILURE_THRESHOLD = 2  # number of consecutive failures before alerting
 
# Track failure counts per carrier
failure_counters = {carrier: 0 for carrier in MONITORED_CARRIERS}
# Track if we've already logged an incident
logged_incidents = {carrier: False for carrier in MONITORED_CARRIERS}
 
 
class CarrierStatusMonitor:
    """Monitor carrier status pages and log incidents to Notion"""
    
    def __init__(self, notion_api_key: str, database_id: str):
        self.notion_api_key = notion_api_key
        self.database_id = database_id
    
    def fetch_status_page(self, carrier: str, url: str) -> Optional[Dict]:
        """
        Fetch a carrier's status page and parse the status
        Returns: {'status': 'operational'|'degraded'|'major', 'incidents': int, 'timestamp': str}
        """
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Parse based on carrier
            status = 'operational'
            incident_count = 0
            
            if 'cogent' in url.lower():
                status = self._parse_cogent_status(soup)
            elif 'lumen' in url.lower():
                status = self._parse_lumen_status(soup)
            elif 'microsoft' in url.lower():
                status = self._parse_microsoft_status(soup)
            
            return {
                'carrier': carrier,
                'status': status,
                'url': url,
                'timestamp': datetime.now().isoformat()
            }
        
        except requests.RequestException as e:
            logger.error(f"Error fetching {carrier} status page: {e}")
            return None
    
    def _parse_cogent_status(self, soup: BeautifulSoup) -> str:
        """Parse Cogent status page"""
        # Look for status indicators on the page
        status_text = soup.get_text().lower()
        
        if 'major' in status_text or 'outage' in status_text:
            return 'major'
        elif 'degraded' in status_text or 'maintenance' in status_text:
            return 'degraded'
        else:
            return 'operational'
    
    def _parse_lumen_status(self, soup: BeautifulSoup) -> str:
        """Parse Lumen Statuspage.io status"""
        # Statuspage.io uses specific CSS classes for status
        status_indicators = soup.find_all('span', class_='component-status')
        
        if not status_indicators:
            # Fallback: check page text
            status_text = soup.get_text().lower()
            if 'major' in status_text or 'outage' in status_text:
                return 'major'
            elif 'degraded' in status_text or 'partial' in status_text:
                return 'degraded'
            return 'operational'
        
        # Check for any major incidents
        for indicator in status_indicators:
            text = indicator.get_text().lower()
            if 'major' in text or 'down' in text:
                return 'major'
            elif 'degraded' in text or 'partial' in text:
                return 'degraded'
        
        return 'operational'
    
    def _parse_microsoft_status(self, soup: BeautifulSoup) -> str:
        """Parse Microsoft 365 status page"""
        # Look for incident indicators
        status_text = soup.get_text().lower()
        
        if 'service degradation' in status_text or 'service incident' in status_text:
            if 'critical' in status_text or 'major' in status_text:
                return 'major'
            else:
                return 'degraded'
        else:
            return 'operational'
    
    def check_existing_incident(self, carrier: str) -> Optional[str]:
        """Check if an active incident already exists in Notion for this carrier"""
        try:
            session = requests.Session()
            session.headers.update({
                'Authorization': f'Bearer {self.notion_api_key}',
                'Notion-Version': NOTION_API_VERSION,
                'Content-Type': 'application/json'
            })
            
            query_url = f'{NOTION_API_ENDPOINT}/databases/{self.database_id}/query'
            
            payload = {
                'filter': {
                    'and': [
                        {
                            'property': 'Service Name',
                            'select': {'equals': carrier}
                        },
                        {
                            'property': 'Status',
                            'select': {'equals': 'Active'}
                        }
                    ]
                }
            }
            
            response = session.post(query_url, json=payload, timeout=10)
            
            if response.status_code == 200:
                results = response.json().get('results', [])
                return results[0]['id'] if results else None
            else:
                logger.warning(f"Notion query returned {response.status_code}")
                return None
        
        except Exception as e:
            logger.error(f"Error checking existing incident for {carrier}: {e}")
            return None
    
    def log_incident_to_notion(self, carrier: str, status: str) -> bool:
        """Log a carrier outage incident to Notion"""
        try:
            session = requests.Session()
            session.headers.update({
                'Authorization': f'Bearer {self.notion_api_key}',
                'Notion-Version': NOTION_API_VERSION,
                'Content-Type': 'application/json'
            })
            
            url = f'{NOTION_API_ENDPOINT}/pages'
            
            # Determine impact level based on status
            if status == 'major':
                impact_level = 'Critical'
            elif status == 'degraded':
                impact_level = 'High'
            else:
                impact_level = 'Medium'
            
            payload = {
                'parent': {'database_id': self.database_id},
                'properties': {
                    'Name': {
                        'title': [
                            {
                                'text': {
                                    'content': f"{carrier} - {status.title()} Outage - {datetime.now().strftime('%b %d, %Y %I:%M %p')}"
                                }
                            }
                        ]
                    },
                    'Service Name': {
                        'select': {'name': carrier}
                    },
                    'Status': {
                        'select': {'name': 'Active'}
                    },
                    'Impact Level': {
                        'select': {'name': impact_level}
                    },
                    'Incident Type': {
                        'select': {'name': 'Network Outage'}
                    },
                    'Notes': {
                        'rich_text': [
                            {
                                'text': {
                                    'content': f"Auto-detected from {carrier} status page. Status: {status}."
                                }
                            }
                        ]
                    },
                    'DownDetector Link': {
                        'url': MONITORED_CARRIERS[carrier]
                    }
                }
            }
            
            response = session.post(url, json=payload, timeout=10)
            response.raise_for_status()
            
            page_id = response.json()['id']
            logger.info(f"✓ Logged {carrier} incident to Notion: {page_id}")
            return True
        
        except Exception as e:
            logger.error(f"Error logging incident to Notion: {e}")
            return False
    
    def update_incident_status(self, page_id: str, status: str) -> bool:
        """Update an incident's status in Notion"""
        try:
            session = requests.Session()
            session.headers.update({
                'Authorization': f'Bearer {self.notion_api_key}',
                'Notion-Version': NOTION_API_VERSION,
                'Content-Type': 'application/json'
            })
            
            url = f'{NOTION_API_ENDPOINT}/pages/{page_id}'
            
            payload = {
                'properties': {
                    'Status': {
                        'select': {'name': status}
                    }
                }
            }
            
            response = session.patch(url, json=payload, timeout=10)
            response.raise_for_status()
            
            logger.info(f"✓ Updated incident {page_id} status to {status}")
            return True
        
        except Exception as e:
            logger.error(f"Error updating incident status: {e}")
            return False
    
    def run_monitoring_cycle(self):
        """Execute one full monitoring cycle"""
        logger.info("=" * 60)
        logger.info("Starting Carrier Status Monitoring Cycle")
        logger.info("=" * 60)
        
        for carrier, url in MONITORED_CARRIERS.items():
            logger.info(f"\nChecking {carrier}...")
            
            # Fetch status from carrier's status page
            status_data = self.fetch_status_page(carrier, url)
            
            if not status_data:
                failure_counters[carrier] += 1
                logger.warning(f"  ✗ Could not fetch {carrier} status. Failure count: {failure_counters[carrier]}")
                
                if failure_counters[carrier] >= FAILURE_THRESHOLD and not logged_incidents[carrier]:
                    logger.error(f"  🚨 {carrier} status check failed {FAILURE_THRESHOLD} times. Logging as incident.")
                    self.log_incident_to_notion(carrier, 'major')
                    logged_incidents[carrier] = True
                continue
            
            status = status_data['status']
            
            # If status is not operational, it's an outage
            if status != 'operational':
                failure_counters[carrier] += 1
                logger.warning(f"  ⚠️ {carrier} status: {status} (Failure count: {failure_counters[carrier]})")
                
                # Check if incident already exists
                existing_incident = self.check_existing_incident(carrier)
                
                # Only log if threshold reached and not already logged
                if failure_counters[carrier] >= FAILURE_THRESHOLD and not logged_incidents[carrier]:
                    logger.error(f"  🚨 {carrier} has {status} outage. Logging to Notion.")
                    if self.log_incident_to_notion(carrier, status):
                        logged_incidents[carrier] = True
            
            else:
                # Status is operational
                if failure_counters[carrier] > 0:
                    logger.info(f"  ✅ {carrier} recovered to operational status.")
                    
                    # Mark as resolved if incident was logged
                    if logged_incidents[carrier]:
                        existing_incident = self.check_existing_incident(carrier)
                        if existing_incident:
                            self.update_incident_status(existing_incident, 'Resolved')
                
                failure_counters[carrier] = 0
                logged_incidents[carrier] = False
                logger.info(f"  ✓ {carrier} status: operational")
        
        logger.info("\n" + "=" * 60)
        logger.info("Monitoring cycle complete")
        logger.info("=" * 60 + "\n")
 
 
def main():
    """Main entry point"""
    
    if NOTION_API_KEY == 'your_notion_api_key_here':
        logger.error("ERROR: NOTION_API_KEY environment variable not set")
        return False
    
    monitor = CarrierStatusMonitor(NOTION_API_KEY, NOTION_DATABASE_ID)
    
    logger.info("🚀 Carrier Status Monitor Started")
    logger.info(f"Monitoring: {', '.join(MONITORED_CARRIERS.keys())}")
    logger.info(f"Poll interval: {POLL_INTERVAL} seconds")
    logger.info(f"Failure threshold: {FAILURE_THRESHOLD} consecutive failures\n")
    
    try:
        while True:
            monitor.run_monitoring_cycle()
            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        logger.info("Stopping Carrier Status Monitor.")
        return True
 
 
if __name__ == '__main__':
    success = main()
    exit(0 if success else 1)
