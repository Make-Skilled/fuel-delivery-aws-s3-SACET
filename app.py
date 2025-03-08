from flask import Flask, render_template, request, redirect, url_for, flash, session, send_from_directory
from pymongo import MongoClient
from werkzeug.security import generate_password_hash, check_password_hash
from bson import ObjectId
from datetime import datetime
import os
from werkzeug.utils import secure_filename
import boto3
from botocore.exceptions import ClientError

app = Flask(__name__)
app.secret_key = 'your-secret-key-here'  # Change this to a secure secret key

# MongoDB connection
client = MongoClient('mongodb://localhost:27017/')
db = client['fuel_delivery']
users = db['users']
owners = db['owners']
stations = db['stations']
orders = db['orders']

# Create indexes for email uniqueness
users.create_index('email', unique=True)
owners.create_index('email', unique=True)

# AWS S3 Configuration
S3_BUCKET = 'fuel-delivery-sacet'
AWS_REGION = 'us-east-1'  # Mumbai region
s3_client = boto3.client('s3', region_name=AWS_REGION)

# Upload folder configuration
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}

# Create upload folder if it doesn't exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def upload_to_s3(file, filename):
    try:
        # Set content type based on file extension
        content_type = file.content_type
        if not content_type:
            ext = filename.rsplit('.', 1)[1].lower()
            if ext in ['jpg', 'jpeg']:
                content_type = 'image/jpeg'
            elif ext == 'png':
                content_type = 'image/png'
        
        # Upload file to S3 with public-read ACL
        s3_client.upload_fileobj(
            file,
            S3_BUCKET,
            filename,
            ExtraArgs={
                'ContentType': content_type,
                'ACL': 'public-read'
            }
        )
        
        # Generate URL for the uploaded file
        url = f"https://{S3_BUCKET}.s3.{AWS_REGION}.amazonaws.com/{filename}"
        return url
    except Exception as e:
        print(f"Error uploading to S3: {str(e)}")
        return None

@app.route('/')
def index():
    return render_template('index.html')

# User Registration and Login Routes
@app.route('/register', methods=['GET'])
def register():
    return render_template('register.html')

@app.route('/register/user', methods=['POST'])
def register_user():
    try:
        user_data = {
            'name': request.form['name'],
            'email': request.form['email'],
            'password': generate_password_hash(request.form['password']),
            'phone': request.form['phone'],
            'type': 'user',
            'created_at': datetime.utcnow()
        }
        users.insert_one(user_data)
        flash('Registration successful! Please login.', 'success')
        return redirect(url_for('login'))
    except Exception as e:
        if 'duplicate key error' in str(e):
            flash('Email already registered. Please use a different email.', 'error')
        else:
            flash('Registration failed. Please try again.', 'error')
        return redirect(url_for('register'))

@app.route('/register/owner', methods=['POST'])
def register_owner():
    try:
        owner_data = {
            'name': request.form['owner_name'],
            'email': request.form['email'],
            'password': generate_password_hash(request.form['password']),
            'phone': request.form['phone'],
            'type': 'owner',
            'created_at': datetime.utcnow()
        }
        
        station_data = {
            'name': request.form['station_name'],
            'address': request.form['address'],
            'city': request.form['city'],
            'phone': request.form['phone'],
            'petrol_quantity': 0,
            'diesel_quantity': 0,
            'petrol_price': 0,
            'diesel_price': 0,
            'created_at': datetime.utcnow()
        }
        
        # Insert owner and get their ID
        owner_id = owners.insert_one(owner_data).inserted_id
        
        # Add owner_id to station data and insert
        station_data['owner_id'] = owner_id
        stations.insert_one(station_data)
        
        flash('Registration successful! Please login.', 'success')
        return redirect(url_for('login'))
    except Exception as e:
        if 'duplicate key error' in str(e):
            flash('Email already registered. Please use a different email.', 'error')
        else:
            flash('Registration failed. Please try again.', 'error')
        return redirect(url_for('register'))

@app.route('/login', methods=['GET'])
def login():
    return render_template('login.html')

@app.route('/login/user', methods=['POST'])
def login_user():
    email = request.form['email']
    password = request.form['password']
    
    user = users.find_one({'email': email})
    
    if user and check_password_hash(user['password'], password):
        session['user_id'] = str(user['_id'])
        session['user_type'] = 'user'
        session['name'] = user['name']
        flash('Login successful!', 'success')
        return redirect(url_for('user_dashboard'))
    
    flash('Invalid email or password.', 'error')
    return redirect(url_for('login'))

@app.route('/login/owner', methods=['POST'])
def login_owner():
    email = request.form['email']
    password = request.form['password']
    
    owner = owners.find_one({'email': email})
    
    if owner and check_password_hash(owner['password'], password):
        session['user_id'] = str(owner['_id'])
        session['user_type'] = 'owner'
        session['name'] = owner['name']
        flash('Login successful!', 'success')
        return redirect(url_for('owner_dashboard'))
    
    flash('Invalid email or password.', 'error')
    return redirect(url_for('login'))

@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.', 'success')
    return redirect(url_for('login'))

# Owner Dashboard and Fuel Management Routes
@app.route('/owner/dashboard')
def owner_dashboard():
    if session.get('user_type') != 'owner':
        flash('Access denied. Please login as a station owner.', 'error')
        return redirect(url_for('login'))
    
    owner_id = ObjectId(session['user_id'])
    station = stations.find_one({'owner_id': owner_id})
    recent_orders = list(orders.find({'station_id': str(station['_id'])}).sort('created_at', -1).limit(10))
    
    return render_template('owner_dashboard.html', station=station, recent_orders=recent_orders)

@app.route('/owner/update_petrol', methods=['POST'])
def update_petrol():
    if session.get('user_type') != 'owner':
        flash('Access denied.', 'error')
        return redirect(url_for('login'))
    
    owner_id = ObjectId(session['user_id'])
    quantity = float(request.form['quantity'])
    price = float(request.form['price'])
    
    stations.update_one(
        {'owner_id': owner_id},
        {'$set': {
            'petrol_quantity': quantity,
            'petrol_price': price,
            'last_updated': datetime.utcnow()
        }}
    )
    
    flash('Petrol details updated successfully.', 'success')
    return redirect(url_for('owner_dashboard'))

@app.route('/owner/update_diesel', methods=['POST'])
def update_diesel():
    if session.get('user_type') != 'owner':
        flash('Access denied.', 'error')
        return redirect(url_for('login'))
    
    owner_id = ObjectId(session['user_id'])
    quantity = float(request.form['quantity'])
    price = float(request.form['price'])
    
    stations.update_one(
        {'owner_id': owner_id},
        {'$set': {
            'diesel_quantity': quantity,
            'diesel_price': price,
            'last_updated': datetime.utcnow()
        }}
    )
    
    flash('Diesel details updated successfully.', 'success')
    return redirect(url_for('owner_dashboard'))

# User Dashboard and Station Search Routes
@app.route('/user/dashboard')
def user_dashboard():
    if session.get('user_type') != 'user':
        flash('Access denied. Please login as a user.', 'error')
        return redirect(url_for('login'))
    
    # Get all unique cities
    cities = stations.distinct('city')
    
    # Get search parameters
    selected_city = request.args.get('city')
    fuel_type = request.args.get('fuel_type', 'petrol')
    
    # Find stations based on search criteria
    if selected_city:
        station_list = list(stations.find({'city': selected_city}))
    else:
        station_list = []
    
    # Get user's recent orders
    user_id = session['user_id']
    recent_orders = list(orders.find({'user_id': user_id}).sort('created_at', -1).limit(10))
    
    return render_template('user_dashboard.html',
                         user={'name': session['name']},
                         cities=cities,
                         selected_city=selected_city,
                         fuel_type=fuel_type,
                         stations=station_list,
                         recent_orders=recent_orders)

@app.route('/search_stations')
def search_stations():
    if session.get('user_type') != 'user':
        flash('Access denied. Please login as a user.', 'error')
        return redirect(url_for('login'))
    
    city = request.args.get('city')
    fuel_type = request.args.get('fuel_type', 'petrol')
    
    # Find stations based on search criteria
    if city:
        station_list = list(stations.find({'city': city}))
    else:
        station_list = []
    
    # Get user's recent orders
    user_id = session['user_id']
    recent_orders = list(orders.find({'user_id': user_id}).sort('created_at', -1).limit(10))
    
    return render_template('user_dashboard.html',
                         user={'name': session['name']},
                         cities=stations.distinct('city'),
                         selected_city=city,
                         fuel_type=fuel_type,
                         stations=station_list,
                         recent_orders=recent_orders)

@app.route('/my_orders')
def my_orders():
    if session.get('user_type') != 'user':
        flash('Access denied. Please login as a user.', 'error')
        return redirect(url_for('login'))
    
    user_id = session['user_id']
    user_orders = list(orders.find({'user_id': user_id}).sort('created_at', -1))
    
    return render_template('my_orders.html',
                         orders=user_orders,
                         user={'name': session['name']})

@app.route('/place_order', methods=['POST'])
def place_order():
    if session.get('user_type') != 'user':
        flash('Access denied. Please login as a user.', 'error')
        return redirect(url_for('login'))
    
    try:
        # Get and validate form data
        station_id = request.form.get('station_id')
        fuel_type = request.form.get('fuel_type', '').lower()
        
        if not station_id:
            flash('Invalid station selected.', 'error')
            return redirect(url_for('user_dashboard'))
        
        if not fuel_type or fuel_type not in ['petrol', 'diesel']:
            flash('Please select a valid fuel type (petrol or diesel).', 'error')
            return redirect(url_for('user_dashboard'))
        
        try:
            quantity = float(request.form.get('quantity', 0))
            if quantity <= 0:
                flash('Please enter a valid quantity.', 'error')
                return redirect(url_for('user_dashboard'))
        except ValueError:
            flash('Invalid quantity value.', 'error')
            return redirect(url_for('user_dashboard'))
        
        # Get station details
        station = stations.find_one({'_id': ObjectId(station_id)})
        if not station:
            flash('Station not found.', 'error')
            return redirect(url_for('user_dashboard'))
        
        # Check fuel availability and get correct price
        if fuel_type == 'petrol':
            if quantity > station['petrol_quantity']:
                flash('Not enough petrol available.', 'error')
                return redirect(url_for('user_dashboard'))
            price = station['petrol_price']
            quantity_field = 'petrol_quantity'
        else:  # diesel
            if quantity > station['diesel_quantity']:
                flash('Not enough diesel available.', 'error')
                return redirect(url_for('user_dashboard'))
            price = station['diesel_price']
            quantity_field = 'diesel_quantity'
        
        # Create order
        order_data = {
            'user_id': session['user_id'],
            'station_id': station_id,
            'station_name': station['name'],
            'station_address': station['address'],
            'fuel_type': fuel_type,
            'quantity': quantity,
            'price_per_liter': price,
            'total_amount': price * quantity,
            'status': 'pending',
            'payment_confirmed': False,
            'created_at': datetime.utcnow(),
            'status_history': [
                {
                    'status': 'pending',
                    'timestamp': datetime.utcnow(),
                    'note': f'Order placed for {quantity}L of {fuel_type}'
                }
            ]
        }
        
        # Insert order
        order_id = orders.insert_one(order_data).inserted_id
        
        # Update station fuel quantity
        stations.update_one(
            {'_id': ObjectId(station_id)},
            {'$inc': {quantity_field: -quantity}}
        )
        
        flash('Order placed successfully!', 'success')
        return redirect(url_for('order_tracking', order_id=str(order_id)))
    
    except Exception as e:
        print(f"Error placing order: {str(e)}")  # Add logging for debugging
        flash('Failed to place order. Please try again.', 'error')
        return redirect(url_for('user_dashboard'))

@app.route('/order/<order_id>')
def order_tracking(order_id):
    if not session.get('user_id'):
        flash('Please login to view order details.', 'error')
        return redirect(url_for('login'))
    
    order = orders.find_one({'_id': ObjectId(order_id)})
    if not order:
        flash('Order not found.', 'error')
        return redirect(url_for('user_dashboard'))
    
    # Get station details
    station = stations.find_one({'_id': ObjectId(order['station_id'])})
    order['station'] = station
    
    # Check if user has permission to view this order
    if session['user_type'] == 'user':
        if order['user_id'] != session['user_id']:
            flash('Access denied. You can only view your own orders.', 'error')
            return redirect(url_for('user_dashboard'))
    elif session['user_type'] == 'owner':
        owner_station = stations.find_one({'owner_id': ObjectId(session['user_id'])})
        if not owner_station or str(owner_station['_id']) != order['station_id']:
            flash('Access denied. You can only view orders from your station.', 'error')
            return redirect(url_for('owner_dashboard'))
    
    return render_template('order_tracking.html', order=order)

@app.route('/order/<order_id>/cancel', methods=['POST'])
def cancel_order(order_id):
    if not session.get('user_id'):
        flash('Please login to cancel orders.', 'error')
        return redirect(url_for('login'))
    
    try:
        order = orders.find_one({'_id': ObjectId(order_id)})
        if not order:
            flash('Order not found.', 'error')
            return redirect(url_for('user_dashboard'))
        
        # Check if user has permission to cancel this order
        if (session['user_type'] == 'user' and order['user_id'] != session['user_id']) or \
           (session['user_type'] == 'owner' and order['station_id'] != session['user_id']):
            flash('Access denied.', 'error')
            return redirect(url_for('user_dashboard'))
        
        # Check if order can be cancelled (only pending orders)
        if order['status'] != 'pending':
            flash('Only pending orders can be cancelled.', 'error')
            return redirect(url_for('order_tracking', order_id=order_id))
        
        # Return fuel quantity to station
        stations.update_one(
            {'_id': ObjectId(order['station_id'])},
            {'$inc': {
                f"{order['fuel_type']}_quantity": order['quantity']
            }}
        )
        
        # Update order status
        orders.update_one(
            {'_id': ObjectId(order_id)},
            {
                '$set': {'status': 'cancelled'},
                '$push': {
                    'status_history': {
                        'status': 'cancelled',
                        'timestamp': datetime.utcnow(),
                        'note': f"Order cancelled by {'user' if session['user_type'] == 'user' else 'station owner'}"
                    }
                }
            }
        )
        
        flash('Order cancelled successfully.', 'success')
        return redirect(url_for('my_orders' if session['user_type'] == 'user' else 'owner_dashboard'))
    
    except Exception as e:
        flash('Failed to cancel order. Please try again.', 'error')
        return redirect(url_for('order_tracking', order_id=order_id))

@app.route('/order/<order_id>/confirm', methods=['POST'])
def confirm_order(order_id):
    if session.get('user_type') != 'owner':
        flash('Access denied. Only station owners can confirm orders.', 'error')
        return redirect(url_for('login'))
    
    try:
        order = orders.find_one({'_id': ObjectId(order_id)})
        if not order:
            flash('Order not found.', 'error')
            return redirect(url_for('owner_dashboard'))
        
        # Check if owner has permission to confirm this order
        station = stations.find_one({'owner_id': ObjectId(session['user_id'])})
        if not station or str(station['_id']) != order['station_id']:
            flash('Access denied. You can only confirm orders for your station.', 'error')
            return redirect(url_for('owner_dashboard'))
        
        # Check if order can be confirmed (only pending orders)
        if order['status'] != 'pending':
            flash('Only pending orders can be confirmed.', 'error')
            return redirect(url_for('order_tracking', order_id=order_id))
        
        # Update order status
        orders.update_one(
            {'_id': ObjectId(order_id)},
            {
                '$set': {'status': 'confirmed'},
                '$push': {
                    'status_history': {
                        'status': 'confirmed',
                        'timestamp': datetime.utcnow(),
                        'note': 'Order confirmed by station owner'
                    }
                }
            }
        )
        
        flash('Order confirmed successfully!', 'success')
        return redirect(url_for('owner_dashboard'))
    
    except Exception as e:
        flash('Failed to confirm order. Please try again.', 'error')
        return redirect(url_for('order_tracking', order_id=order_id))

@app.route('/order/<order_id>/mark_out_for_delivery', methods=['POST'])
def mark_out_for_delivery(order_id):
    if session.get('user_type') != 'owner':
        flash('Access denied. Only station owners can update order status.', 'error')
        return redirect(url_for('login'))
    
    try:
        order = orders.find_one({'_id': ObjectId(order_id)})
        if not order:
            flash('Order not found.', 'error')
            return redirect(url_for('owner_dashboard'))
        
        # Check if owner has permission
        station = stations.find_one({'owner_id': ObjectId(session['user_id'])})
        if not station or str(station['_id']) != order['station_id']:
            flash('Access denied. You can only update orders for your station.', 'error')
            return redirect(url_for('owner_dashboard'))
        
        # Check if order can be marked as out for delivery (only confirmed orders)
        if order['status'] != 'confirmed':
            flash('Only confirmed orders can be marked as out for delivery.', 'error')
            return redirect(url_for('order_tracking', order_id=order_id))
        
        # Update order status
        orders.update_one(
            {'_id': ObjectId(order_id)},
            {
                '$set': {'status': 'out_for_delivery'},
                '$push': {
                    'status_history': {
                        'status': 'out_for_delivery',
                        'timestamp': datetime.utcnow(),
                        'note': 'Order marked as out for delivery'
                    }
                }
            }
        )
        
        flash('Order marked as out for delivery!', 'success')
        return redirect(url_for('owner_dashboard'))
    
    except Exception as e:
        flash('Failed to update order status. Please try again.', 'error')
        return redirect(url_for('order_tracking', order_id=order_id))

@app.route('/order/<order_id>/mark_delivered', methods=['POST'])
def mark_delivered(order_id):
    if session.get('user_type') != 'owner':
        flash('Access denied. Only station owners can update order status.', 'error')
        return redirect(url_for('login'))
    
    try:
        order = orders.find_one({'_id': ObjectId(order_id)})
        if not order:
            flash('Order not found.', 'error')
            return redirect(url_for('owner_dashboard'))
        
        # Check if owner has permission
        station = stations.find_one({'owner_id': ObjectId(session['user_id'])})
        if not station or str(station['_id']) != order['station_id']:
            flash('Access denied. You can only update orders for your station.', 'error')
            return redirect(url_for('owner_dashboard'))
        
        # Check if order can be marked as delivered (only out_for_delivery orders)
        if order['status'] != 'out_for_delivery':
            flash('Only orders that are out for delivery can be marked as delivered.', 'error')
            return redirect(url_for('order_tracking', order_id=order_id))
        
        # Update order status
        orders.update_one(
            {'_id': ObjectId(order_id)},
            {
                '$set': {'status': 'delivered'},
                '$push': {
                    'status_history': {
                        'status': 'delivered',
                        'timestamp': datetime.utcnow(),
                        'note': 'Order marked as delivered'
                    }
                }
            }
        )
        
        flash('Order marked as delivered!', 'success')
        return redirect(url_for('owner_dashboard'))
    
    except Exception as e:
        flash('Failed to update order status. Please try again.', 'error')
        return redirect(url_for('order_tracking', order_id=order_id))

@app.route('/order/<order_id>/confirm_payment', methods=['POST'])
def confirm_payment(order_id):
    if session.get('user_type') != 'user':
        flash('Access denied. Only users can confirm payments.', 'error')
        return redirect(url_for('login'))
    
    try:
        order = orders.find_one({'_id': ObjectId(order_id)})
        if not order:
            flash('Order not found.', 'error')
            return redirect(url_for('my_orders'))
        
        # Check if user has permission
        if order['user_id'] != session['user_id']:
            flash('Access denied. You can only confirm payments for your own orders.', 'error')
            return redirect(url_for('my_orders'))
        
        # Check if order is delivered
        if order['status'] != 'delivered':
            flash('Payment can only be confirmed for delivered orders.', 'error')
            return redirect(url_for('order_tracking', order_id=order_id))
        
        # Handle receipt upload
        if 'payment_receipt' not in request.files:
            flash('No receipt file uploaded.', 'error')
            return redirect(url_for('order_tracking', order_id=order_id))
        
        receipt = request.files['payment_receipt']
        if receipt.filename == '':
            flash('No receipt file selected.', 'error')
            return redirect(url_for('order_tracking', order_id=order_id))
        
        if receipt and allowed_file(receipt.filename):
            try:
                # Generate unique filename with order ID and timestamp
                timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
                ext = receipt.filename.rsplit('.', 1)[1].lower()
                filename = secure_filename(f"receipts/order_{order_id}_{timestamp}.{ext}")
                
                # Upload to S3
                receipt_url = upload_to_s3(receipt, filename)
                
                if not receipt_url:
                    raise Exception("Failed to upload file to S3")
                
                # Get payment note if provided
                payment_note = request.form.get('payment_note', '')
                
                # Update order with receipt info and mark payment as confirmed
                orders.update_one(
                    {'_id': ObjectId(order_id)},
                    {
                        '$set': {
                            'payment_confirmed': True,
                            'payment_receipt': receipt_url,
                            'payment_note': payment_note,
                            'payment_confirmed_at': datetime.utcnow()
                        },
                        '$push': {
                            'status_history': {
                                'status': 'payment_confirmed',
                                'timestamp': datetime.utcnow(),
                                'note': 'Payment confirmed with receipt upload'
                            }
                        }
                    }
                )
                
                flash('Payment confirmed successfully!', 'success')
                return redirect(url_for('order_tracking', order_id=order_id))
                
            except Exception as e:
                print(f"Error processing payment confirmation: {str(e)}")
                flash('Failed to process payment confirmation. Please try again.', 'error')
                return redirect(url_for('order_tracking', order_id=order_id))
        else:
            flash('Invalid file type. Please upload a valid image file (PNG, JPG, JPEG).', 'error')
            return redirect(url_for('order_tracking', order_id=order_id))
    
    except Exception as e:
        print(f"Error confirming payment: {str(e)}")
        flash('Failed to confirm payment. Please try again.', 'error')
        return redirect(url_for('order_tracking', order_id=order_id))

if __name__ == '__main__':
    app.run(debug=True)
