import sqlite3
import datetime
import time
import re
import os
import shutil
from collections import defaultdict

# Local data storage
DB_NAME = "item_tracking.db"

# Item Type Translation (initial values)
INITIAL_ITEM_CODES = {
    "PIPI": "PioPino",
    "CHNU": "Chestnut",
    "KIOY": "KingOyster",
    "BLOY": "BlueOyster",
    "PIOY": "PinkOyster",
    "LIMA": "Lionsmane",
    "INVE": "Inventory",
    "STOR": "Storage",
    "MISC": "Miscellaneous"
}

# ===== TASK 1: DATABASE BACKUP =====
def backup_database():
    """Backup database on program start"""
    backup_dir = "backup"
    if not os.path.exists(backup_dir):
        os.makedirs(backup_dir)
    
    now = datetime.datetime.now()
    timestamp = now.strftime("%S_%M_%H_%d_%m_%y")
    backup_name = f"{timestamp}.db"
    backup_path = os.path.join(backup_dir, backup_name)
    
    try:
        shutil.copyfile(DB_NAME, backup_path)
        print(f"Database backed up to: {backup_path}")
    except Exception as e:
        print(f"Backup failed: {e}")

# ===== TASK 3: VALIDATION FUNCTIONS =====
def is_alphanumeric(input_str):
    """Check if input is alphanumeric"""
    return input_str.isalnum()

def to_upper_alphanumeric(input_str):
    """Convert to uppercase and remove non-alphanumeric characters"""
    return ''.join(filter(str.isalnum, input_str)).upper()

# Helper function to detect item barcodes
def looks_like_item_barcode(barcode):
    """Check if barcode matches item format (XXXX_DD_MM_YY_GX_XXXX)"""
    if not barcode:
        return False
    parts = barcode.split('_')
    return len(parts) in (5, 6)  # 5 parts = batch, 6 parts = item

# Updated database initialization
def init_database():
    conn = sqlite3.connect(DB_NAME, timeout=15)
    c = conn.cursor()
    c.execute("PRAGMA journal_mode=WAL;")  # Enable Write-Ahead Logging
    c.execute("PRAGMA foreign_keys = ON;")  # Enable foreign key constraints
    
    # Table for batch scans
    c.execute('''CREATE TABLE IF NOT EXISTS batch_scans
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                 batch_barcode TEXT,
                 item_type TEXT,
                 generation TEXT,
                 created_date TEXT,
                 quantity INTEGER,
                 scan_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                 status TEXT)''')
    
    # Table for individual items
    c.execute('''CREATE TABLE IF NOT EXISTS items
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                 full_barcode TEXT UNIQUE,
                 batch_barcode TEXT,
                 item_type TEXT,
                 generation TEXT,
                 created_date TEXT,
                 current_status TEXT DEFAULT 'IN')''')
    
    # Table for notes (now on individual items)
    c.execute('''CREATE TABLE IF NOT EXISTS notes
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                 item_id INTEGER,
                 note TEXT,
                 FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE)''')
    
    # Table for locations
    c.execute('''CREATE TABLE IF NOT EXISTS locations
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                 barcode TEXT UNIQUE,
                 location_name TEXT)''')
    
    # Table for location assignments (now on individual items)
    c.execute('''CREATE TABLE IF NOT EXISTS item_locations
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                 item_id INTEGER,
                 location_barcode TEXT,
                 timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                 FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE,
                 FOREIGN KEY (location_barcode) REFERENCES locations(barcode))''')
    
    # Table for scans (renamed from 'scans' to avoid conflict)
    c.execute('''CREATE TABLE IF NOT EXISTS item_scans
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                 barcode TEXT,
                 item_type TEXT,
                 generation TEXT,
                 created_date TEXT,
                 scan_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                 status TEXT)''')
    
    # Table for item codes
    c.execute('''CREATE TABLE IF NOT EXISTS item_codes
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                 code TEXT UNIQUE,
                 name TEXT)''')
    
    # Insert initial item codes if table is empty
    c.execute("SELECT COUNT(*) FROM item_codes")
    if c.fetchone()[0] == 0:
        for code, name in INITIAL_ITEM_CODES.items():
            c.execute("INSERT INTO item_codes (code, name) VALUES (?, ?)", (code, name))
    
    # Indexes for performance
    c.execute("CREATE INDEX IF NOT EXISTS idx_batch_barcode ON batch_scans (batch_barcode)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_full_barcode ON items (full_barcode)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_batch_items ON items (batch_barcode)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_item_location ON item_locations (item_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_scan_barcode ON item_scans (barcode)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_item_status ON items (current_status)")
    
    conn.commit()
    conn.close()

def get_item_name(code):
    """Get item name from code using database"""
    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    try:
        c.execute("SELECT name FROM item_codes WHERE code = ?", (code,))
        result = c.fetchone()
        return result[0] if result else "Unknown"
    except:
        return "Unknown"
    finally:
        conn.close()

def parse_barcode(barcode, is_batch=False):
    """Parse barcode in format XXXX_DD_MM_YY_GX (batch) or XXXX_DD_MM_YY_GX_XXXX (item)"""
    parts = barcode.split('_')
    
    # Validate part count based on barcode type
    if is_batch and len(parts) != 5:
        print(f"Invalid batch barcode: Expected 5 parts, got {len(parts)}")
        return None
    elif not is_batch and len(parts) not in (5, 6):
        print(f"Invalid item barcode: Expected 5 or 6 parts, got {len(parts)}")
        return None
        
    item_code = parts[0]
    day = parts[1]
    month = parts[2]
    year = parts[3]
    generation = parts[4]
    
    # European date format: DD_MM_YY → DD.MM.YYYY
    try:
        full_year = 2000 + int(year) if int(year) < 100 else int(year)
        created_date = f"{day}.{month}.{full_year}"
        
        # Item Type translation from database
        item_type = get_item_name(item_code)
        
        return {
            "item_type": item_type,
            "generation": generation,
            "created_date": created_date,
            "full_barcode": barcode
        }
    except Exception as e:
        print(f"Parsing error: {e}")
        return None

def ensure_item_exists(full_barcode, item_type, generation, created_date):
    """Ensure item exists in items table, create if missing"""
    conn = sqlite3.connect(DB_NAME, timeout=15)
    try:
        c = conn.cursor()
        # Check if item exists
        c.execute("SELECT 1 FROM items WHERE full_barcode = ?", (full_barcode,))
        if c.fetchone():
            return True
            
        # Extract batch barcode (first 5 parts)
        parts = full_barcode.split('_')
        if len(parts) < 5:
            print(f"Invalid barcode format: {full_barcode}")
            return False
            
        batch_barcode = '_'.join(parts[:5])
        
        # Create new item with default status 'IN'
        c.execute('''INSERT INTO items 
                    (full_barcode, batch_barcode, item_type, generation, created_date, current_status)
                    VALUES (?, ?, ?, ?, ?, ?)''',
                (full_barcode, batch_barcode, item_type, generation, created_date, 'IN'))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        # Item already exists (race condition)
        return True
    except Exception as e:
        print(f"Error ensuring item exists: {e}")
        return False
    finally:
        conn.close()

def update_item_status(barcode, new_status):
    """Update the current status of an item"""
    conn = sqlite3.connect(DB_NAME, timeout=15)
    try:
        c = conn.cursor()
        c.execute("UPDATE items SET current_status = ? WHERE full_barcode = ?", (new_status, barcode))
        conn.commit()
        return c.rowcount > 0
    except Exception as e:
        print(f"Error updating status: {e}")
        return False
    finally:
        conn.close()

def log_scan(parsed_data, status, max_retries=3, retry_delay=0.2):
    """Save scan to database with retry on lock"""
    retries = 0
    while retries < max_retries:
        conn = None
        try:
            conn = sqlite3.connect(DB_NAME, timeout=15)
            c = conn.cursor()
            c.execute('''INSERT INTO item_scans 
                        (barcode, item_type, generation, created_date, status)
                        VALUES (?, ?, ?, ?, ?)''',
                    (parsed_data["full_barcode"],
                     parsed_data["item_type"],
                     parsed_data["generation"],
                     parsed_data["created_date"],
                     status))
            conn.commit()
            return True
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e):
                retries += 1
                print(f"Database locked, retrying ({retries}/{max_retries})...")
                time.sleep(retry_delay)
                continue
            print(f"Save error: {e}")
            return False
        except Exception as e:
            print(f"Save error: {e}")
            return False
        finally:
            if conn:
                conn.close()
    print(f"Failed to save scan after {max_retries} retries.")
    return False

def add_note(barcode, note):
    """Add note to a barcode"""
    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    
    try:
        # First get item ID from barcode
        c.execute("SELECT id FROM items WHERE full_barcode = ?", (barcode,))
        item_row = c.fetchone()
        if not item_row:
            print("Item not found!")
            return False
            
        item_id = item_row[0]
        
        # Check if note exists
        c.execute("SELECT * FROM notes WHERE item_id = ?", (item_id,))
        if c.fetchone():
            # Update if exists
            c.execute("UPDATE notes SET note = ? WHERE item_id = ?", (note, item_id))
        else:
            # Create new if doesn't exist
            c.execute("INSERT INTO notes (item_id, note) VALUES (?, ?)", (item_id, note))
        
        conn.commit()
        return True
    except Exception as e:
        print(f"Error adding note: {e}")
        return False
    finally:
        conn.close()

def get_note(barcode):
    """Get note for a barcode"""
    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    
    try:
        # Get note via item ID
        c.execute('''SELECT n.note 
                    FROM notes n
                    JOIN items i ON n.item_id = i.id
                    WHERE i.full_barcode = ?''', (barcode,))
        result = c.fetchone()
        return result[0] if result else ""
    except:
        return ""
    finally:
        conn.close()

def show_last_scan(parsed_data, status):
    """Show details of last scan"""
    note = get_note(parsed_data["full_barcode"])
    
    print("\n" + "="*50)
    print("LAST SCAN:")
    print(f"Type:      {parsed_data['item_type']}")
    print(f"Generation:{parsed_data['generation']}")
    print(f"Date:      {parsed_data['created_date']}")
    print(f"Status:    {status}")
    print(f"Note:      {note}")
    print(f"Barcode:   {parsed_data['full_barcode']}")
    print("="*50)

# ===== TASK 3: LOCATION VALIDATION =====
def register_location(barcode, location_name):
    """Register new location with alphanumeric validation"""
    # Clean and validate barcode
    clean_barcode = to_upper_alphanumeric(barcode)
    if not clean_barcode:
        print("Invalid location barcode: Must contain alphanumeric characters")
        return False
        
    # Validate location name
    if not location_name.strip():
        print("Location name cannot be empty")
        return False
        
    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    
    try:
        c.execute('''INSERT OR REPLACE INTO locations 
                    (barcode, location_name)
                    VALUES (?, ?)''',
                (clean_barcode, location_name))
        
        conn.commit()
        return True
    except Exception as e:
        print(f"Error registering location: {e}")
        return False
    finally:
        conn.close()

def move_item_to_location(item_barcode, location_barcode, max_retries=3, retry_delay=0.2):
    """Move item to location with retry on lock"""
    # Clean location barcode
    clean_location_barcode = to_upper_alphanumeric(location_barcode)
    
    retries = 0
    while retries < max_retries:
        conn = None
        try:
            conn = sqlite3.connect(DB_NAME, timeout=15)
            c = conn.cursor()
            
            # Check if location exists
            c.execute("SELECT 1 FROM locations WHERE barcode = ?", (clean_location_barcode,))
            if not c.fetchone():
                print("Location not registered! Please register first.")
                return False
                
            # Get item ID
            c.execute("SELECT id FROM items WHERE full_barcode = ?", (item_barcode,))
            item_row = c.fetchone()
            if not item_row:
                print("Item not found in database! Creating now...")
                # Parse barcode to get item details
                parsed = parse_barcode(item_barcode)
                if not parsed:
                    return False
                # Ensure item exists
                if not ensure_item_exists(
                    item_barcode, 
                    parsed['item_type'], 
                    parsed['generation'], 
                    parsed['created_date']
                ):
                    print("Failed to create item")
                    return False
                    
                # Try to get item ID again
                c.execute("SELECT id FROM items WHERE full_barcode = ?", (item_barcode,))
                item_row = c.fetchone()
                if not item_row:
                    print("Item still not found after creation attempt!")
                    return False
                    
            item_id = item_row[0]
            
            # Create location assignment
            c.execute('''INSERT INTO item_locations 
                        (item_id, location_barcode)
                        VALUES (?, ?)''',
                    (item_id, clean_location_barcode))
            
            # Log as IN scan
            parsed = parse_barcode(item_barcode)
            if parsed:
                c.execute('''INSERT INTO item_scans 
                            (barcode, item_type, generation, created_date, status)
                            VALUES (?, ?, ?, ?, ?)''',
                        (item_barcode, 
                         parsed['item_type'], 
                         parsed['generation'], 
                         parsed['created_date'], 
                         'IN'))
            
            conn.commit()
            return True
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e):
                retries += 1
                print(f"Database locked, retrying ({retries}/{max_retries})...")
                time.sleep(retry_delay)
                continue
            print(f"Error assigning item: {e}")
            return False
        except Exception as e:
            print(f"Error assigning item: {e}")
            return False
        finally:
            if conn:
                conn.close()
    print(f"Failed to move item after {max_retries} retries.")
    return False

def get_current_location(item_barcode):
    """Get current location of an item"""
    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    
    try:
        c.execute('''SELECT l.location_name 
                    FROM item_locations il
                    JOIN locations l ON il.location_barcode = l.barcode
                    JOIN items i ON il.item_id = i.id
                    WHERE i.full_barcode = ?
                    ORDER BY il.timestamp DESC
                    LIMIT 1''', (item_barcode,))
        result = c.fetchone()
        return result[0] if result else "No location"
    except:
        return "Error"
    finally:
        conn.close()

def generate_inventory_report():
    """Generate live inventory report based on current status"""
    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    
    try:
        # Calculate inventory from items table
        c.execute('''SELECT 
                    item_type, 
                    generation, 
                    COUNT(*) as total,
                    SUM(CASE WHEN current_status = 'IN' THEN 1 ELSE 0 END) as in_stock,
                    SUM(CASE WHEN current_status = 'OUT' THEN 1 ELSE 0 END) as out
                    FROM items
                    GROUP BY item_type, generation''')
        
        results = c.fetchall()
        
        # Generate report
        print("\n" + "="*60)
        print("CURRENT INVENTORY REPORT")
        print(f"Date: {datetime.datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")
        print("="*60)
        print(f"{'Type':<15} {'Gen':<5} {'Total':<7} {'In Stock':<11} {'Out':<11} {'Available':<9}")
        print("-"*60)
        
        for row in results:
            item_type, gen, total, in_stock, out = row
            # Available = In Stock (only IN items are available)
            print(f"{item_type:<15} {gen:<5} {total:<7} {in_stock:<11} {out:<11} {in_stock:<9}")
        
        print("="*60)
    finally:
        conn.close()

def generate_detailed_report(item_type=None, generation=None, location_barcode=None, date=None):
    """Generate detailed report with filtering options - shows latest status per item"""
    # Initialize location display name
    loc_display = location_barcode if location_barcode else None
    
    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    
    try:
        # Get latest scan for each item
        query = """SELECT 
                    i.full_barcode,
                    i.item_type,
                    i.generation,
                    i.created_date,
                    s.scan_time,
                    i.current_status,
                    COALESCE(n.note, '') as note,
                    COALESCE(l.location_name, 'No location') as location_name
                FROM items i
                LEFT JOIN (
                    SELECT barcode, MAX(scan_time) as max_time
                    FROM item_scans
                    GROUP BY barcode
                ) latest ON i.full_barcode = latest.barcode
                LEFT JOIN item_scans s ON s.barcode = i.full_barcode AND s.scan_time = latest.max_time
                LEFT JOIN notes n ON n.item_id = i.id
                LEFT JOIN (
                    SELECT il.item_id, l.location_name
                    FROM item_locations il
                    JOIN locations l ON il.location_barcode = l.barcode
                    WHERE il.timestamp = (
                        SELECT MAX(timestamp)
                        FROM item_locations
                        WHERE item_id = il.item_id
                    )
                ) l ON l.item_id = i.id"""
        
        params = []
        conditions = []
        
        if item_type:
            conditions.append("i.item_type = ?")
            params.append(item_type)
        if generation:
            conditions.append("i.generation = ?")
            params.append(generation)
        if location_barcode:
            clean_location_barcode = to_upper_alphanumeric(location_barcode)
            if clean_location_barcode:
                # Try to get location name
                c.execute("SELECT location_name FROM locations WHERE barcode = ?", (clean_location_barcode,))
                result = c.fetchone()
                if result:
                    loc_display = result[0]
                    conditions.append("l.location_name = ?")
                    params.append(loc_display)
                else:
                    loc_display = f"Not Found: {clean_location_barcode}"
            else:
                loc_display = f"Invalid: {location_barcode}"
        if date:
            conditions.append("i.created_date = ?")
            params.append(date)
        
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        
        c.execute(query, params)
        results = c.fetchall()
        
        # Generate report
        print("\n" + "="*120)
        print("DETAILED ITEM REPORT (LATEST STATUS)")
        if item_type:
            print(f"Filter: Type = {item_type}")
        if generation:
            print(f"Filter: Generation = {generation}")
        if location_barcode:
            print(f"Filter: Location = {loc_display}")
        if date:
            print(f"Filter: Created Date = {date}")
        print("="*120)
        print(f"{'Scan Time':<19} {'Type':<12} {'Gen':<5} {'Status':<8} {'Create Date':<12} {'Location':<15} {'Barcode':<20} {'Note':<30}")
        print("-"*120)
        
        for row in results:
            scan_time = row[4]
            if scan_time:
                try:
                    # Convert to datetime object if it's a string
                    if isinstance(scan_time, str):
                        scan_time = datetime.datetime.strptime(scan_time, "%Y-%m-%d %H:%M:%S")
                    formatted_time = scan_time.strftime("%d.%m.%Y %H:%M")
                except:
                    formatted_time = "N/A"
            else:
                formatted_time = "N/A"
                
            print(f"{formatted_time:<19} {row[1]:<12} {row[2]:<5} {row[5]:<8} {row[3]:<12} {row[7]:<15} {row[0]:<20} {row[6]:<30}")
        
        print(f"\nTotal entries: {len(results)}")
        print("="*120)
    except Exception as e:
        print(f"Error generating report: {e}")
    finally:
        conn.close()

def get_highest_item_number():
    """Find the highest item number across all batches"""
    conn = sqlite3.connect(DB_NAME, timeout=15)
    try:
        c = conn.cursor()
        c.execute("SELECT full_barcode FROM items")
        existing_barcodes = [row[0] for row in c.fetchall()]
        
        max_num = 0
        for barcode in existing_barcodes:
            parts = barcode.split('_')
            if len(parts) == 6:
                try:
                    # Extract the numeric part at the end
                    num_part = parts[5]
                    if num_part.isdigit():
                        num = int(num_part)
                        if num > max_num:
                            max_num = num
                except ValueError:
                    continue
        return max_num
    except Exception as e:
        print(f"Error finding highest item number: {e}")
        return 0
    finally:
        conn.close()

def create_batch():
    """Create a new batch with unique item IDs and assign location"""
    print("\nBATCH CREATION MODE")
    
    # FIRST: Scan location barcode
    location_barcode = input("Scan location barcode for this batch: ").strip().upper()
    if not location_barcode:
        print("Location is required for batch creation!")
        return
        
    # Validate location exists
    conn = sqlite3.connect(DB_NAME, timeout=15)
    c = conn.cursor()
    try:
        c.execute("SELECT 1 FROM locations WHERE barcode = ?", (location_barcode,))
        if not c.fetchone():
            print("Location not registered! Please register first.")
            return
    except:
        print("Error validating location")
        return
    finally:
        conn.close()
    
    # THEN: Scan batch barcode
    barcode_input = input("Scan batch or item barcode: ").strip()
    
    # Parse the barcode
    parsed = parse_barcode(barcode_input)
    if not parsed:
        print("Invalid barcode format!")
        return
    
    # Extract batch base from barcode
    parts = barcode_input.split('_')
    if len(parts) == 6:
        batch_base = '_'.join(parts[:5])
    elif len(parts) == 5:
        batch_base = barcode_input
    else:
        print("Invalid barcode format! Must be 5 or 6 parts")
        return
    
    # Find highest existing item number across ALL batches
    max_num = get_highest_item_number()
    start_num = max_num + 1
    
    # Get quantity to create
    try:
        quantity = int(input(f"Quantity (starting from #{start_num}): "))
        if quantity <= 0:
            print("Quantity must be positive")
            return
    except:
        print("Invalid quantity")
        return
    
    # Create batch record
    conn = sqlite3.connect(DB_NAME, timeout=15)
    try:
        c = conn.cursor()
        # Insert batch scan record
        c.execute('''INSERT INTO batch_scans 
                    (batch_barcode, item_type, generation, created_date, quantity, status)
                    VALUES (?, ?, ?, ?, ?, ?)''',
                (batch_base, parsed['item_type'], parsed['generation'], 
                 parsed['created_date'], quantity, 'CREATED'))
        
        # Create new items
        for i in range(start_num, start_num + quantity):
            suffix = f"{i:04d}"
            full_barcode = f"{batch_base}_{suffix}"
            
            # Insert item
            c.execute('''INSERT INTO items 
                        (full_barcode, batch_barcode, item_type, generation, created_date, current_status)
                        VALUES (?, ?, ?, ?, ?, ?)''',
                    (full_barcode, batch_base, parsed['item_type'], 
                     parsed['generation'], parsed['created_date'], 'IN'))
            
            # Get item ID
            item_id = c.lastrowid
            
            # Assign location
            c.execute('''INSERT INTO item_locations 
                        (item_id, location_barcode)
                        VALUES (?, ?)''',
                    (item_id, location_barcode))
            
            # Log as IN scan
            c.execute('''INSERT INTO item_scans 
                        (barcode, item_type, generation, created_date, status)
                        VALUES (?, ?, ?, ?, ?)''',
                    (full_barcode, parsed['item_type'], parsed['generation'], 
                     parsed['created_date'], 'IN'))
        
        conn.commit()
        print(f"Created {quantity} items for batch {batch_base} at location {location_barcode}")
        print(f"Items range: {batch_base}_{start_num:04d} to {batch_base}_{start_num+quantity-1:04d}")
        
    except sqlite3.IntegrityError as e:
        conn.rollback()
        print(f"Database error: {e}. Batch creation aborted.")
    except Exception as e:
        conn.rollback()
        print(f"Error: {e}")
    finally:
        conn.close()

# ===== TASK 4: ADDED FINISH NOTE TO PROMPT =====
def move_item_session():
    """Move items to new location using single DB connection"""
    print("\nMOVE ITEMS MODE - Scan items")
    print("First scan TARGET location barcode (e.g. 'TENT1')")
    print("Scan/type 'finish' to return to menu")
    
    # Scan target location
    target_location = input("\nScan target location barcode: ").strip().upper()
    if not target_location:
        print("No target location specified!")
        return
    
    # Check for item barcodes scanned as locations
    if looks_like_item_barcode(target_location):
        print("Error: Scanned barcode appears to be an ITEM barcode.")
        print("Please scan a LOCATION barcode instead.")
        return
    
    # Single connection for entire session
    conn = sqlite3.connect(DB_NAME, timeout=15)
    try:
        c = conn.cursor()
        
        # Validate location ONCE and get name
        c.execute("SELECT location_name FROM locations WHERE barcode = ?", (target_location,))
        result = c.fetchone()
        if not result:
            print("Location not registered! Please register first.")
            return
        location_name = result[0]
        print(f"Target location: {location_name}")

        while True:
            barcode = input("\nScan item barcode (or 'finish' to exit): ").strip()
            
            if barcode.lower() == "finish":
                break
                
            # Parse barcode to ensure we can create item if needed
            parsed = parse_barcode(barcode)
            if not parsed:
                continue
                
            # Move item using existing connection
            try:
                # Ensure item exists
                c.execute("SELECT id, current_status FROM items WHERE full_barcode = ?", (barcode,))
                item_row = c.fetchone()
                if not item_row:
                    print("Item not found! Creating now...")
                    # Extract batch barcode (first 5 parts)
                    parts = barcode.split('_')
                    if len(parts) < 5:
                        print("Invalid barcode format")
                        continue
                    batch_barcode = '_'.join(parts[:5])
                    
                    # Create new item with status IN
                    c.execute('''INSERT INTO items 
                                (full_barcode, batch_barcode, item_type, generation, created_date, current_status)
                                VALUES (?, ?, ?, ?, ?, ?)''',
                            (barcode, batch_barcode, parsed['item_type'], 
                             parsed['generation'], parsed['created_date'], 'IN'))
                    
                    # Get the new item ID
                    c.execute("SELECT id FROM items WHERE full_barcode = ?", (barcode,))
                    item_row = c.fetchone()
                    if not item_row:
                        print("Failed to create item")
                        continue
                    item_id = item_row[0]
                else:
                    item_id, current_status = item_row
                    # If item was checked out, change status to IN
                    if current_status == 'OUT':
                        c.execute("UPDATE items SET current_status = 'IN' WHERE id = ?", (item_id,))
                        print("Item status changed to IN")

                # Create location assignment
                c.execute('''INSERT INTO item_locations 
                            (item_id, location_barcode)
                            VALUES (?, ?)''',
                        (item_id, target_location))
                
                # Log as IN scan
                c.execute('''INSERT INTO item_scans 
                            (barcode, item_type, generation, created_date, status)
                            VALUES (?, ?, ?, ?, ?)''',
                        (barcode, parsed['item_type'], parsed['generation'], 
                         parsed['created_date'], 'IN'))
                
                conn.commit()
                print(f"✓ Item moved to {location_name}")
                
            except sqlite3.OperationalError as e:
                if "database is locked" in str(e):
                    print("Database busy, retrying...")
                    conn.rollback()
                    time.sleep(0.2)  # Short delay before retry
                    continue
                print(f"Error moving item: {e}")
            except Exception as e:
                print(f"Error moving item: {e}")
                conn.rollback()
    finally:
        conn.close()  # Ensure connection closes

def list_locations():
    """List all registered locations"""
    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    
    try:
        c.execute("SELECT barcode, location_name FROM locations")
        locations = c.fetchall()
        
        if not locations:
            print("No locations registered yet!")
            return
            
        print("\n" + "="*40)
        print("REGISTERED LOCATIONS")
        print("="*40)
        print(f"{'Barcode':<15} {'Location Name':<25}")
        print("-"*40)
        for loc in locations:
            print(f"{loc[0]:<15} {loc[1]:<25}")
        print("="*40)
    except Exception as e:
        print(f"Error listing locations: {e}")
    finally:
        conn.close()

def remove_location():
    """Remove a location from the system"""
    barcode = input("Scan location barcode to remove: ").strip().upper()
    if not barcode:
        print("No barcode provided!")
        return
        
    conn = sqlite3.connect(DB_NAME, timeout=10)
    try:
        c = conn.cursor()
        
        # Check if location exists
        c.execute("SELECT location_name FROM locations WHERE barcode = ?", (barcode,))
        result = c.fetchone()
        if not result:
            print("Location not found!")
            return
            
        location_name = result[0]
        
        # Check if location has assigned items
        c.execute("SELECT COUNT(*) FROM item_locations WHERE location_barcode = ?", (barcode,))
        count = c.fetchone()[0]
        
        if count > 0:
            print(f"Cannot remove '{location_name}' - it has {count} items assigned!")
            return
            
        # Delete location
        c.execute("DELETE FROM locations WHERE barcode = ?", (barcode,))
        conn.commit()
        print(f"Location '{location_name}' removed successfully!")
        
    except Exception as e:
        print(f"Error removing location: {e}")
    finally:
        conn.close()

def list_item_codes():
    """List all registered item codes"""
    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    
    try:
        c.execute("SELECT code, name FROM item_codes")
        codes = c.fetchall()
        
        if not codes:
            print("No item codes registered yet!")
            return
            
        print("\n" + "="*40)
        print("REGISTERED ITEM CODES")
        print("="*40)
        print(f"{'Code':<10} {'Item Name':<25}")
        print("-"*40)
        for code in codes:
            print(f"{code[0]:<10} {code[1]:<25}")
        print("="*40)
    except Exception as e:
        print(f"Error listing item codes: {e}")
    finally:
        conn.close()

# ===== TASK 3: ITEM CODE VALIDATION =====
def add_or_update_item_code():
    """Add or update an item code"""
    print("\nADD/UPDATE ITEM CODE")
    code = input("Item code (e.g. 'PIPI'): ").strip().upper()
    name = input("Item name (e.g. 'Pio Pino'): ").strip()
    
    if not code or not name:
        print("Both fields are required!")
        return
    
    # Validate alphanumeric
    if not is_alphanumeric(code):
        print("Item code must be alphanumeric!")
        return
        
    conn = sqlite3.connect(DB_NAME, timeout=10)
    try:
        c = conn.cursor()
        
        # Check if code exists
        c.execute("SELECT 1 FROM item_codes WHERE code = ?", (code,))
        exists = c.fetchone()
        
        if exists:
            c.execute("UPDATE item_codes SET name = ? WHERE code = ?", (name, code))
            action = "updated"
        else:
            c.execute("INSERT INTO item_codes (code, name) VALUES (?, ?)", (code, name))
            action = "added"
            
        conn.commit()
        print(f"Item code '{code}' successfully {action}!")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        conn.close()

def remove_item_code():
    """Remove an item code from the system"""
    code = input("Enter item code to remove: ").strip().upper()
    if not code:
        print("No code provided!")
        return
        
    conn = sqlite3.connect(DB_NAME, timeout=10)
    try:
        c = conn.cursor()
        
        # Check if code exists
        c.execute("SELECT name FROM item_codes WHERE code = ?", (code,))
        result = c.fetchone()
        if not result:
            print("Item code not found!")
            return
            
        name = result[0]
        
        # Check if code is used in any items
        c.execute("SELECT COUNT(*) FROM items WHERE item_type = ?", (name,))
        count = c.fetchone()[0]
        
        if count > 0:
            print(f"Cannot remove '{code}' - it has {count} items associated with it!")
            return
            
        # Delete code
        c.execute("DELETE FROM item_codes WHERE code = ?", (code,))
        conn.commit()
        print(f"Item code '{code}' removed successfully!")
        
    except Exception as e:
        print(f"Error removing item code: {e}")
    finally:
        conn.close()

def manage_locations():
    """Location management menu"""
    while True:
        print("\n" + "="*30)
        print("LOCATION MANAGEMENT")
        print("="*30)
        print("1: Register new location")
        print("2: List all locations")
        print("3: Remove a location")
        print("4: Back to main menu")
        
        choice = input("Select: ").strip()
        
        if choice == "1":
            register_location_session()
        elif choice == "2":
            list_locations()
        elif choice == "3":
            remove_location()
        elif choice == "4":
            break
        else:
            print("Invalid selection!")

def manage_item_codes():
    """Item code management menu"""
    while True:
        print("\n" + "="*30)
        print("ITEM CODE MANAGEMENT")
        print("="*30)
        print("1: List all item codes")
        print("2: Add/Update item code")
        print("3: Remove item code")
        print("4: Back to main menu")
        
        choice = input("Select: ").strip()
        
        if choice == "1":
            list_item_codes()
        elif choice == "2":
            add_or_update_item_code()
        elif choice == "3":
            remove_item_code()
        elif choice == "4":
            break
        else:
            print("Invalid selection!")

def register_location_session():
    """Register new location with validation"""
    print("\nREGISTER NEW LOCATION")
    location_barcode = input("Location barcode: ").strip().upper()
    location_name = input("Location name (e.g. 'Shelf 1'): ").strip()
    
    if not location_barcode or not location_name:
        print("Invalid input!")
        return
        
    # Validate alphanumeric
    if not is_alphanumeric(location_barcode):
        print("Location barcode must be alphanumeric!")
        return
        
    if register_location(location_barcode, location_name):
        print(f"Location {location_name} successfully registered!")
    else:
        print("Error registering location!")

# ===== TASK 2: MULTIPLE NOTE ENTRY =====
def add_notes_session():
    """Add notes to multiple items continuously"""
    print("\nADD NOTES MODE - Scan items (type 'finish' to exit)")
    print("Scan/type 'finish' to return to menu")
    
    while True:
        barcode = input("\nScan barcode (or 'finish' to exit): ").strip()
        if barcode.lower() == "finish":
            break
            
        # Check if barcode exists
        conn = sqlite3.connect(DB_NAME, timeout=10)
        c = conn.cursor()
        try:
            c.execute("SELECT * FROM items WHERE full_barcode = ?", (barcode,))
            if not c.fetchone():
                print("Barcode not found! Skipping.")
                continue
        finally:
            conn.close()
        
        note = input(f"Enter note for {barcode}: ").strip()
        
        if add_note(barcode, note):
            print("Note added/updated successfully!")
        else:
            print("Error saving note!")

def delete_all_out_items():
    """Delete all items with OUT status and their associated data"""
    print("\nWARNING: This will permanently delete ALL items marked as OUT!")
    confirm = input("Are you sure? (type 'DELETE ALL' to confirm): ").strip()
    
    # Case-insensitive comparison with typo tolerance
    normalized_confirm = ''.join(filter(str.isalpha, confirm.upper()))
    if normalized_confirm != "DELETEALL":
        print("Operation cancelled.")
        return

    conn = sqlite3.connect(DB_NAME, timeout=15)
    try:
        c = conn.cursor()
        # Get count of OUT items BEFORE deletion
        c.execute("SELECT COUNT(*) FROM items WHERE current_status = 'OUT'")
        out_count = c.fetchone()[0]
        
        if out_count == 0:
            print("No OUT items found. Nothing deleted.")
            return
            
        # Delete all related data for OUT items
        # 1. Delete notes for OUT items
        c.execute("DELETE FROM notes WHERE item_id IN (SELECT id FROM items WHERE current_status = 'OUT')")
        
        # 2. Delete location history for OUT items
        c.execute("DELETE FROM item_locations WHERE item_id IN (SELECT id FROM items WHERE current_status = 'OUT')")
        
        # 3. Delete scan history for OUT items
        c.execute("DELETE FROM item_scans WHERE barcode IN (SELECT full_barcode FROM items WHERE current_status = 'OUT')")
        
        # 4. Finally delete the OUT items themselves
        c.execute("DELETE FROM items WHERE current_status = 'OUT'")
        
        conn.commit()
        print(f"Deleted {out_count} OUT items and all their associated data.")
    except Exception as e:
        conn.rollback()
        print(f"Error deleting OUT items: {e}")
    finally:
        conn.close()

def main():
    init_database()
    # ===== TASK 1: BACKUP ON START =====
    backup_database()
    
    while True:
        print("\n" + "="*30)
        print("ITEM TRACKING SYSTEM")
        print("="*30)
        print("1: Check in / Move items")
        print("2: Check out items (OUT)")
        print("3: Manage item codes")
        print("4: Manage locations")
        print("5: Show detailed report")
        print("6: Add/edit note")
        print("7: Create new batch")
        print("8: Delete all OUT items")
        print("9: Exit")
        
        choice = input("Select: ")
        
        if choice == "1":
            move_item_session()
        elif choice == "2":
            # For OUT, we don't need location
            print("\nCHECK OUT MODE - Scan items (type 'finish' to exit)")
            print("Scan/type 'finish' to return to menu")
            while True:
                barcode = input("\nScan item barcode (or 'finish' to exit): ").strip()
                
                if barcode.lower() == "finish":
                    break
                    
                # Parse as individual barcode (6 parts)
                parsed = parse_barcode(barcode)
                if not parsed:
                    print("Invalid barcode! Expected format: XXXX_DD_MM_YY_GX_XXXX")
                    continue
                
                # Ensure item exists in database
                if not ensure_item_exists(
                    barcode, 
                    parsed['item_type'], 
                    parsed['generation'], 
                    parsed['created_date']
                ):
                    print("Failed to ensure item exists in database")
                    continue
                
                # Update item status to OUT
                if update_item_status(barcode, 'OUT'):
                    print("Item status updated to OUT")
                
                # Log the scan
                if log_scan(parsed, 'OUT'):
                    show_last_scan(parsed, 'OUT')
                    generate_inventory_report()
                else:
                    print("Error saving scan!")
        elif choice == "3":
            manage_item_codes()
        elif choice == "4":
            manage_locations()
        elif choice == "5":
            print("\nFilter options (leave blank for all):")
            i_type = input("Item type: ").strip()
            gen = input("Generation: ").strip()
            loc = input("Location barcode: ").strip()
            date_filter = input("Created date (DD.MM.YYYY): ").strip()
            generate_detailed_report(
                i_type if i_type else None,
                gen if gen else None,
                loc if loc else None,
                date_filter if date_filter else None
            )
        elif choice == "6":
            # ===== TASK 2: UPDATED NOTE FUNCTION =====
            add_notes_session()
        elif choice == "7":
            create_batch()
        elif choice == "8":
            delete_all_out_items()
        elif choice == "9":
            print("Exiting system...")
            break
        else:
            print("Invalid selection!")

if __name__ == "__main__":
    main()