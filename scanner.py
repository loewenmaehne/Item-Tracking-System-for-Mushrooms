import sqlite3
import datetime
import time
import re
from collections import defaultdict

# GDPR-compliant local data storage
DB_NAME = "item_tracking.db"

# Item Type Translation
ITEM_CODES = {
    "PiPi": "PioPino",
    "ChNu": "Chestnut",
    "KiOy": "KingOyster",
    "BlOy": "BlueOyster",
    "PiOy": "PinkOyster",
    "LiMa": "Lionsmane"
}

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
    
    # Indexes for performance
    c.execute("CREATE INDEX IF NOT EXISTS idx_batch_barcode ON batch_scans (batch_barcode)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_full_barcode ON items (full_barcode)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_batch_items ON items (batch_barcode)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_item_location ON item_locations (item_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_scan_barcode ON item_scans (barcode)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_item_status ON items (current_status)")
    
    conn.commit()
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
        
        # Item Type translation
        item_type = ITEM_CODES.get(item_code, "Unknown")
        
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

def register_location(barcode, location_name):
    """Register new location"""
    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    
    try:
        c.execute('''INSERT INTO locations 
                    (barcode, location_name)
                    VALUES (?, ?)''',
                (barcode, location_name))
        
        conn.commit()
        return True
    except Exception as e:
        print(f"Error registering location: {e}")
        return False
    finally:
        conn.close()

def move_item_to_location(item_barcode, location_barcode, max_retries=3, retry_delay=0.2):
    """Move item to location with retry on lock"""
    retries = 0
    while retries < max_retries:
        conn = None
        try:
            conn = sqlite3.connect(DB_NAME, timeout=15)
            c = conn.cursor()
            
            # Check if location exists
            c.execute("SELECT 1 FROM locations WHERE barcode = ?", (location_barcode,))
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
                    (item_id, location_barcode))
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

def generate_detailed_report(item_type=None, generation=None, location_barcode=None):
    """Generate detailed report with filtering options - shows latest status per item"""
    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    
    try:
        # Get latest scan for each item
        query = """SELECT 
                    i.full_barcode,
                    i.item_type,
                    i.generation,
                    i.created_date,
                    MAX(s.scan_time) as latest_scan_time,
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
            # First get location name for display
            c.execute("SELECT location_name FROM locations WHERE barcode = ?", (location_barcode,))
            loc_name = c.fetchone()
            loc_name = loc_name[0] if loc_name else location_barcode
            
            conditions.append("l.location_name = ?")
            params.append(loc_name)
        
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        
        query += " GROUP BY i.full_barcode"
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
            print(f"Filter: Location = {loc_name}")
        print("="*120)
        print(f"{'Scan Time':<19} {'Type':<12} {'Gen':<5} {'Status':<8} {'Create Date':<12} {'Location':<15} {'Barcode':<20} {'Note':<30}")
        print("-"*120)
        
        for row in results:
            scan_time = datetime.datetime.strptime(row[4], "%Y-%m-%d %H:%M:%S").strftime("%d.%m.%Y %H:%M") if row[4] else "N/A"
            print(f"{scan_time:<19} {row[1]:<12} {row[2]:<5} {row[5]:<8} {row[3]:<12} {row[7]:<15} {row[0]:<20} {row[6]:<30}")
        
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
    location_barcode = input("Scan location barcode for this batch: ").strip()
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

def scan_session(status):
    """Process multiple scans in a session with location validation"""
    print(f"\n{' CHECK IN ' if status == 'IN' else ' CHECK OUT '} MODE - Scan items (type 'finish' to exit)")
    
    # For check-in, require location; for check-out, location is optional
    location_barcode = None
    if status == 'IN':
        location_barcode = input("\nScan location barcode: ").strip()
        if not location_barcode:
            print("Location is required for check-in!")
            return
        
        # Check for item barcodes scanned as locations
        if looks_like_item_barcode(location_barcode):
            print("Error: Scanned barcode appears to be an ITEM barcode.")
            print("Please scan a LOCATION barcode instead.")
            return
    
    # Validate location exists if provided
    if location_barcode:
        conn = sqlite3.connect(DB_NAME, timeout=10)
        c = conn.cursor()
        try:
            c.execute("SELECT 1 FROM locations WHERE barcode = ?", (location_barcode,))
            if not c.fetchone():
                print("Location not registered! Please register first.")
                conn.close()
                return
        finally:
            conn.close()
    
    while True:
        barcode = input("\nScan item barcode: ").strip()
        
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
        
        # Update item status
        if update_item_status(barcode, status):
            print(f"Item status updated to {status}")
        
        # For check-in, move to location
        if status == 'IN' and location_barcode:
            if move_item_to_location(parsed["full_barcode"], location_barcode):
                print(f"Item moved to location {location_barcode}")
        
        # Log the scan
        if log_scan(parsed, status):
            show_last_scan(parsed, status)
            generate_inventory_report()
        else:
            print("Error saving scan!")

def move_item_session():
    """Move items to new location using single DB connection"""
    print("\nMOVE ITEM MODE - Scan items (type 'finish' to exit)")
    print("First scan TARGET location barcode (e.g. 'GROWTENT1')")
    
    # Scan target location
    target_location = input("\nScan target location barcode: ").strip()
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
            barcode = input("\nScan item barcode: ").strip()
            
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

def register_location_session():
    """Register new location"""
    print("\nREGISTER NEW LOCATION")
    location_barcode = input("Location barcode: ").strip()
    location_name = input("Location name (e.g. 'Shelf 1'): ").strip()
    
    if not location_barcode or not location_name:
        print("Invalid input!")
        return
        
    if register_location(location_barcode, location_name):
        print(f"Location {location_name} successfully registered!")
    else:
        print("Error registering location!")

def add_note_to_barcode():
    """Add note to existing barcode"""
    barcode = input("Scan barcode for note: ").strip()
    
    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    
    try:
        # Check if barcode exists
        c.execute("SELECT * FROM items WHERE full_barcode = ?", (barcode,))
        if not c.fetchone():
            print("Barcode not found!")
            return
    finally:
        conn.close()
    
    note = input("Enter note: ").strip()
    
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
    
    while True:
        print("\n" + "="*30)
        print("ITEM TRACKING SYSTEM")
        print("="*30)
        print("1: Check in item (IN)")
        print("2: Check out item (OUT)")
        print("3: Move item")
        print("4: Register location")
        print("5: Show detailed report")
        print("6: Add/edit note")
        print("7: Create new batch")
        print("8: Delete all OUT items")
        print("9: Exit")
        
        choice = input("Select: ")
        
        if choice == "1":
            scan_session("IN")
        elif choice == "2":
            scan_session("OUT")
        elif choice == "3":
            move_item_session()
        elif choice == "4":
            register_location_session()
        elif choice == "5":
            print("\nFilter options (leave blank for all):")
            i_type = input("Item type: ").strip()
            gen = input("Generation: ").strip()
            loc = input("Location barcode: ").strip()
            generate_detailed_report(
                i_type if i_type else None,
                gen if gen else None,
                loc if loc else None
            )
        elif choice == "6":
            add_note_to_barcode()
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
