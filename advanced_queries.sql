-- ============================================================================
-- API PULSE: ADVANCED SQL DEMONSTRATION QUERIES
-- Deliverables: Nested, Correlated, Recursive, CTEs, Joins, and Stored Procedures
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. MULTI-TABLE JOIN QUERY
-- Joins 5 tables: users, roles, subscriptions, plans, services, and usage_limits
-- ----------------------------------------------------------------------------
SELECT 
    u.user_id,
    u.name AS developer_name,
    r.role_name,
    p.plan_name,
    p.price AS monthly_fee,
    s.service_name,
    s.provider,
    l.limit_type,
    l.max_requests
FROM users u
JOIN roles r ON r.role_id = u.role_id
JOIN user_subscriptions us ON us.user_id = u.user_id AND us.status = 'active'
JOIN plans p ON p.plan_id = us.plan_id
JOIN api_services s ON s.user_id = u.user_id
LEFT JOIN usage_limits l ON l.service_id = s.service_id
ORDER BY u.user_id, s.service_id;


-- ----------------------------------------------------------------------------
-- 2. NESTED SUBQUERY (Uncorrelated)
-- Finds all services that belong to developers enrolled in a plan that costs
-- more than the average plan price.
-- ----------------------------------------------------------------------------
SELECT 
    s.service_id,
    s.service_name,
    s.provider,
    u.name AS owner_name
FROM api_services s
JOIN users u ON u.user_id = s.user_id
WHERE u.user_id IN (
    SELECT us.user_id
    FROM user_subscriptions us
    JOIN plans p ON p.plan_id = us.plan_id
    WHERE p.price > (SELECT AVG(price) FROM plans)
);


-- ----------------------------------------------------------------------------
-- 3. CORRELATED SUBQUERY
-- Finds each service whose monthly request limit exceeds or matches the
-- average monthly limit for that specific provider.
-- (The inner query references outer row `s1.provider`)
-- ----------------------------------------------------------------------------
SELECT 
    s1.service_id,
    s1.service_name,
    s1.provider,
    l1.max_requests
FROM api_services s1
JOIN usage_limits l1 ON l1.service_id = s1.service_id
WHERE l1.max_requests >= (
    SELECT AVG(l2.max_requests)
    FROM api_services s2
    JOIN usage_limits l2 ON l2.service_id = s2.service_id
    WHERE s2.provider = s1.provider
);


-- ----------------------------------------------------------------------------
-- 4. COMMON TABLE EXPRESSIONS (CTEs) WITH AGGREGATIONS
-- Calculates user-level API quotas and ranks services per user.
-- ----------------------------------------------------------------------------
WITH UserQuotaSummary AS (
    SELECT 
        u.user_id,
        u.name AS owner_name,
        p.plan_name,
        COUNT(s.service_id) AS total_services,
        SUM(COALESCE(l.max_requests, 0)) AS total_allocated_requests
    FROM users u
    JOIN user_subscriptions us ON us.user_id = u.user_id AND us.status = 'active'
    JOIN plans p ON p.plan_id = us.plan_id
    LEFT JOIN api_services s ON s.user_id = u.user_id
    LEFT JOIN usage_limits l ON l.service_id = s.service_id
    GROUP BY u.user_id, u.name, p.plan_name
)
SELECT 
    owner_name,
    plan_name,
    total_services,
    total_allocated_requests,
    CASE 
        WHEN total_services >= 2 THEN 'Near Capacity'
        ELSE 'Slots Available'
    END AS tier_status
FROM UserQuotaSummary;


-- ----------------------------------------------------------------------------
-- 5. RECURSIVE SQL QUERY (WITH RECURSIVE)
-- Generates a 30-day time-series timeline window for request log alignment
-- and historical usage projection.
-- ----------------------------------------------------------------------------
WITH RECURSIVE MonitoringTimeline AS (
    -- Anchor member: Starting date 30 days ago
    SELECT (CURRENT_DATE - INTERVAL '29 days')::DATE AS metric_date, 1 AS day_number
    UNION ALL
    -- Recursive member: Increment day by day until current date
    SELECT (metric_date + INTERVAL '1 day')::DATE, day_number + 1
    FROM MonitoringTimeline
    WHERE metric_date < CURRENT_DATE
)
SELECT 
    day_number,
    metric_date,
    TO_CHAR(metric_date, 'Dy') AS day_name,
    TO_CHAR(metric_date, 'Month DD, YYYY') AS formatted_date
FROM MonitoringTimeline
LIMIT 10;


-- ----------------------------------------------------------------------------
-- 6. STORED PROCEDURE
-- Upgrades a user's subscription and records an audit log.
-- (Demonstrates PROCEDURES alongside our existing plpgsql FUNCTION and TRIGGER)
-- ----------------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE sp_upgrade_user_subscription(
    p_user_id INT,
    p_new_plan_name VARCHAR(50)
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_plan_id INT;
BEGIN
    -- Verify target plan exists
    SELECT plan_id INTO v_plan_id FROM plans WHERE plan_name = p_new_plan_name;
    IF v_plan_id IS NULL THEN
        RAISE EXCEPTION 'Target plan % does not exist in plans table.', p_new_plan_name;
    END IF;

    -- Update active subscription
    UPDATE user_subscriptions
    SET plan_id = v_plan_id
    WHERE user_id = p_user_id AND status = 'active';

    RAISE NOTICE 'User ID % successfully migrated to plan % (Plan ID: %).', 
                 p_user_id, p_new_plan_name, v_plan_id;
END;
$$;

-- Example execution of stored procedure:
-- CALL sp_upgrade_user_subscription(2, 'Pro');
