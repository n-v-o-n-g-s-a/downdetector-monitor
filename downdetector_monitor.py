#!/usr/bin/env python3
"""
DownDetector Monitoring Agent for Cityside Fiber NOC
Monitors external ISP/carrier outages and auto-logs to Notion database
Can run on a schedule (cron, AWS Lambda, GitHub Actions, etc.)
"""

import os
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import requests
from bs4 import BeautifulSoup
import time

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
NOTION_API_KEY = os.getenv('NOTION_API_KEY', 'your_notion_api_key_here')
NOTION_DATABASE_ID = 'faf29e8b-81b9-495b-9c85-fb6746525f8d'

# ISPs/carriers to monitor
MONITORED_SERVICES = {
    'Cogent': 'Cogent Communications',
    'CenturyLink': 'CenturyLink',
    'Zito Media': 'Zito Media',
    'Level 3': 'Level 3 Communications',
    'Spectrum': 'Charter Spectrum',
}

DOWNDETECTOR_BASE_URL = 'https://downdetector.com/search/?q='
NOTION_API_ENDPOINT = 'https://api.notion.com/v1'
NOTION_API_VERSION = '2024-06-15'


class DownDetectorMonitor:
    """Monitor DownDetector for ISP outages and log to Notion"""
    
    def __init__(self, notion_api_key: str, database_id: str):
        self.notion_api_key = notion_api_key
        self.database_id = database_id
        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': f'Bearer {self.notion_api_key}',
            'Notion-Version': NOTION_API_VERSION,
            'Content-Type': 'application/json'
        })
    
    def fetch_downdetector_data(self, service_name: str) -> Optional[Dict]:
        """
        Scrape DownDetector for a service
        """
        try:
            url = f'{DOWNDETECTOR_BASE_URL}{service_name}'
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            reports_count = 0
            report_elements = soup.find_all(string=lambda text: text and 'reports' in str(text).lower())
            if report_elements:
                for elem in report_elements:
                    try:
                        text = str(elem).strip()
                        if 'reports' in text.lower():
                            num = ''.join(filter(str.isdigit, text))
                            if num:
                                reports_count = int(num)
                                break
                    except (ValueError, AttributeError):
                        continue
            
            is_active = 'problems' in response.text.lower() and reports_count > 0
            
            return {
                'service': service_name,
                'status': 'Active' if is_active else 'Resolved',
                'reports': reports_count,
                'is_major': reports_count > 20,
                'url': url,
                'last_update': datetime.now().isoformat()
            }
        
        except requests.RequestException as e:
            logger.error(f"Error fetching DownDetector for {service_name}: {e}")
            return None
    
    def check_existing_incident(self, service_name: str) -> Optional[Dict]:
        """Check if an active incident already exists in Notion database"""
        try:
            query_url = f'{NOTION_API_ENDPOINT}/databases/{self.database_id}/query'
            
            payload = {
                'filter': {
                    'and': [
                        {
                            'property': 'Service Name',
                            'select': {'equals': service_name}
                        },
                        {
                            'property': 'Status',
                            'select': {'equals': 'Active'}
                        }
                    ]
                }
            }
            
            response = self.session.post(query_url, json=payload)
            response.raise_for_status()
            
            results = response.json()['results']
            return results[0] if results else None
        
        except requests.RequestException as e:
            logger.error(f"Error querying Notion for {service_name}: {e}")
            return None
    
    def log_incident_to_notion(self, incident_data: Dict) -> bool:
        """
        Log a new incident to Notion database
        """
        try:
            url = f'{NOTION_API_ENDPOINT}/pages'
            
            if incident_data.get('reports', 0) > 50:
                impact_level = 'Critical'
            elif incident_data.get('reports', 0) > 20:
                impact_level = 'High'
            elif incident_data.get('reports', 0) > 5:
                impact_level = 'Medium'
            else:
                impact_level = 'Low'
            
            service_name = incident_data['service']
            if service_name not in MONITORED_SERVICES:
                service_name = 'Other ISP'
            
            payload = {
                'parent': {'database_id': self.database_id},
                'properties': {
                    'Name': {
                        'title': [
                            {
                                'text': {
                                    'content': f"{incident_data['service']} Outage - {datetime.now().strftime('%b %d, %Y %I:%M %p')}"
                                }
                            }
                        ]
                    },
                    'Service Name': {
                        'select': {'name': incident_data['service']}
                    },
                    'Status': {
                        'select': {'name': incident_data.get('status', 'Active')}
                    },
                    'Impact Level': {
                        'select': {'name': impact_level}
                    },
                    'Reports (24h)': {
                        'number': incident_data.get('reports', 0)
                    },
                    'Detected At': {
                        'date': {
                            'start': datetime.now().isoformat(),
                            'time_zone': 'America/Los_Angeles'
                        }
                    },
                    'Notes': {
                        'rich_text': [
                            {
                                'text': {
                                    'content': incident_data.get('notes', 'Auto-detected from DownDetector')
                                }
                            }
                        ]
                    },
                    'DownDetector Link': {
                        'url': incident_data.get('url', '')
                    },
                    'Incident Type': {
                        'select': {'name': incident_data.get('incident_type', 'Network Outage')}
                    }
                }
            }
            
            response = self.session.post(url, json=payload)
            response.raise_for_status()
            
            page_id = response.json()['id']
            logger.info(f"✓ Logged {incident_data['service']} incident to Notion: {page_id}")
            return True
        
        except requests.RequestException as e:
            logger.error(f"Error logging incident to Notion: {e}")
            return False
    
    def update_incident_status(self, page_id: str, status: str) -> bool:
        """Update an incident's status in Notion"""
        try:
            url = f'{NOTION_API_ENDPOINT}/pages/{page_id}'
            
            payload = {
                'properties': {
                    'Status': {
                        'select': {'name': status}
                    }
                }
            }
            
            response = self.session.patch(url, json=payload)
            response.raise_for_status()
            
            logger.info(f"✓ Updated incident {page_id} status to {status}")
            return True
        
        except requests.RequestException as e:
            logger.error(f"Error updating incident status: {e}")
            return False
    
    def run_monitoring_cycle(self) -> Dict[str, int]:
        """
        Execute one full monitoring cycle
        """
        stats = {
            'checked': 0,
            'new_incidents': 0,
            'resolved': 0,
            'errors': 0
        }
        
        logger.info("=" * 60)
        logger.info("Starting DownDetector monitoring cycle")
        logger.info("=" * 60)
        
        for service_key, service_name in MONITORED_SERVICES.items():
            stats['checked'] += 1
            logger.info(f"\nChecking {service_name}...")
            
            dd_data = self.fetch_downdetector_data(service_key)
            
            if not dd_data:
                stats['errors'] += 1
                logger.warning(f"  ✗ Could not fetch data for {service_name}")
                continue
            
            existing_incident = self.check_existing_incident(service_name)
            
            if dd_data['status'] == 'Active' and dd_data['reports'] > 0:
                if not existing_incident:
                    logger.info(f"  🚨 NEW INCIDENT: {dd_data['reports']} reports")
                    
                    incident_data = {
                        'service': service_name,
                        'status': 'Active',
                        'reports': dd_data['reports'],
                        'url': dd_data['url'],
                        'incident_type': 'Network Outage',
                        'notes': f"Auto-detected via DownDetector. {dd_data['reports']} reports in the last 24 hours."
                    }
                    
                    if self.log_incident_to_notion(incident_data):
                        stats['new_incidents'] += 1
                    else:
                        stats['errors'] += 1
                else:
                    logger.info(f"  ℹ Active incident already logged ({dd_data['reports']} reports)")
            
            elif dd_data['status'] == 'Resolved' and existing_incident:
                logger.info(f"  ✓ Marking incident as resolved")
                if self.update_incident_status(existing_incident['id'], 'Resolved'):
                    stats['resolved'] += 1
                else:
                    stats['errors'] += 1
            
            elif dd_data['reports'] == 0:
                logger.info(f"  ✓ {service_name} status: OK (no reports)")
            
            time.sleep(2)
        
        logger.info("\n" + "=" * 60)
        logger.info(f"Monitoring cycle complete:")
        logger.info(f"  Services checked: {stats['checked']}")
        logger.info(f"  New incidents: {stats['new_incidents']}")
        logger.info(f"  Marked resolved: {stats['resolved']}")
        logger.info(f"  Errors: {stats['errors']}")
        logger.info("=" * 60 + "\n")
        
        return stats


def main():
    """Main entry point"""
    
    if NOTION_API_KEY == 'your_notion_api_key_here':
        logger.error("ERROR: NOTION_API_KEY environment variable not set")
        logger.error("Set it with: export NOTION_API_KEY='your_key_here'")
        return False
    
    monitor = DownDetectorMonitor(NOTION_API_KEY, NOTION_DATABASE_ID)
    stats = monitor.run_monitoring_cycle()
    
    return stats['errors'] == 0


if __name__ == '__main__':
    success = main()
    exit(0 if success else 1)
