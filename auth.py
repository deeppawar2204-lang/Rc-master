import bcrypt
import streamlit as st
from crud import get_user_by_email, create_user
from streamlit.errors import StreamlitSecretNotFoundError

def hash_password(password: str) -> str:
    """Generates a secure salt and hashes the plaintext password."""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Compares a plaintext password against the stored bcrypt hash."""
    return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))

def initialize_session():
    """Sets up default empty session states for a new visitor."""
    if 'user_id' not in st.session_state:
        st.session_state['user_id'] = None
    if 'user_email' not in st.session_state:
        st.session_state['user_email'] = None
    if 'role' not in st.session_state:
        st.session_state['role'] = None
    if 'is_authenticated' not in st.session_state:
        st.session_state['is_authenticated'] = False

def authenticate_user(email: str, password: str) -> bool:
    """
    Validates credentials against the database. 
    If successful, injects user context into the Streamlit session.
    """
    user_record = get_user_by_email(email)
    
    if user_record and verify_password(password, user_record['password_hash']):
        st.session_state['user_id'] = user_record['id']
        st.session_state['user_email'] = user_record['email']
        st.session_state['role'] = user_record['role']
        st.session_state['is_authenticated'] = True
        return True
        
    return False

def register_new_user(email: str, password: str, role: str = 'student') -> bool:
    """
    Hashes the password and attempts to create a new user record.
    Returns True if successful, False if the email already exists.
    """
    hashed = hash_password(password)
    user_id = create_user(email, hashed, role)
    
    if user_id:
        return True
    return False


def bootstrap_admin_from_secrets() -> bool:
    """Creates the configured initial administrator without exposing admin signup publicly."""
    try:
        admin_email = st.secrets.get("ADMIN_EMAIL")
        admin_password = st.secrets.get("ADMIN_PASSWORD")
    except StreamlitSecretNotFoundError:
        return False

    if not admin_email or not admin_password or get_user_by_email(admin_email):
        return False
    return register_new_user(admin_email, admin_password, role="admin")


def logout_user():
    """Clears the active session state, forcing a return to the login screen."""
    st.session_state['user_id'] = None
    st.session_state['user_email'] = None
    st.session_state['role'] = None
    st.session_state['is_authenticated'] = False