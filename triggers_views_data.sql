-- ============ TRIGGER: auto-create Free subscription on signup ============
CREATE OR REPLACE FUNCTION create_free_subscription()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO user_subscriptions (user_id, plan_id)
    SELECT NEW.user_id, plan_id FROM plans WHERE plan_name = 'Free';
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_new_user_subscription
AFTER INSERT ON users
FOR EACH ROW
EXECUTE FUNCTION create_free_subscription();

-- ============ VIEW: one row per service with owner, plan and limit ============
CREATE VIEW service_overview AS
SELECT s.service_id,
       s.service_name,
       s.provider,
       u.name       AS owner,
       p.plan_name,
       l.limit_type,
       l.max_requests
FROM api_services s
JOIN users u               ON u.user_id = s.user_id
JOIN user_subscriptions us ON us.user_id = u.user_id AND us.status = 'active'
JOIN plans p               ON p.plan_id = us.plan_id
LEFT JOIN usage_limits l   ON l.service_id = s.service_id;

-- ============ SAMPLE DATA ============
-- Users (the trigger gives each one a Free subscription automatically)
INSERT INTO users (name, email, password_hash, role_id) VALUES
 ('Admin User', 'admin@example.com', 'placeholder_hash_1',
    (SELECT role_id FROM roles WHERE role_name = 'admin')),
 ('Dev User',   'dev@example.com',   'placeholder_hash_2',
    (SELECT role_id FROM roles WHERE role_name = 'user')),
 ('Test User',  'test@example.com',  'placeholder_hash_3',
    (SELECT role_id FROM roles WHERE role_name = 'user'));

-- Services
INSERT INTO api_services (user_id, service_name, provider, base_url, description) VALUES
 ((SELECT user_id FROM users WHERE email = 'dev@example.com'),
  'Stripe Payments', 'Stripe', 'https://api.stripe.com',
  'Accept online payments, create charges and manage subscriptions'),
 ((SELECT user_id FROM users WHERE email = 'dev@example.com'),
  'Twilio SMS', 'Twilio', 'https://api.twilio.com',
  'Send text messages and phone notifications to users'),
 ((SELECT user_id FROM users WHERE email = 'admin@example.com'),
  'OpenAI Chat', 'OpenAI', 'https://api.openai.com',
  'Generate text and answer questions using large language models');

-- Endpoints
INSERT INTO api_endpoints (service_id, path, http_method) VALUES
 ((SELECT service_id FROM api_services WHERE service_name = 'Stripe Payments'), '/v1/charges', 'POST'),
 ((SELECT service_id FROM api_services WHERE service_name = 'Stripe Payments'), '/v1/customers', 'GET'),
 ((SELECT service_id FROM api_services WHERE service_name = 'Twilio SMS'), '/Messages', 'POST'),
 ((SELECT service_id FROM api_services WHERE service_name = 'OpenAI Chat'), '/v1/chat/completions', 'POST');

-- Usage limits
INSERT INTO usage_limits (service_id, limit_type, max_requests) VALUES
 ((SELECT service_id FROM api_services WHERE service_name = 'Stripe Payments'), 'monthly', 1000),
 ((SELECT service_id FROM api_services WHERE service_name = 'Twilio SMS'), 'daily', 200),
 ((SELECT service_id FROM api_services WHERE service_name = 'OpenAI Chat'), 'monthly', 5000);

-- Alerts
INSERT INTO alerts (service_id, metric, threshold) VALUES
 ((SELECT service_id FROM api_services WHERE service_name = 'Stripe Payments'), 'usage_percent', 80),
 ((SELECT service_id FROM api_services WHERE service_name = 'Stripe Payments'), 'error_rate', 5),
 ((SELECT service_id FROM api_services WHERE service_name = 'OpenAI Chat'), 'latency', 2000);