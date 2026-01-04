"""
Billing and payment service using Stripe.
Handles subscription management and usage-based billing.
"""

import stripe
import os
from typing import Dict, Any
from datetime import datetime

# Configure Stripe
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")


class BillingService:
    """Service for handling payments and billing."""

    @staticmethod
    async def create_customer(email: str, name: str) -> Dict[str, Any]:
        """Create a new Stripe customer."""
        try:
            customer = stripe.Customer.create(
                email=email,
                name=name,
                metadata={"created_at": datetime.now().isoformat()}
            )
            return {
                "customer_id": customer.id,
                "email": customer.email
            }
        except stripe.error.StripeError as e:
            raise Exception(f"Failed to create customer: {str(e)}")

    @staticmethod
    async def create_subscription(
        customer_id: str,
        price_id: str,
        tier: str
    ) -> Dict[str, Any]:
        """Create a subscription for a customer."""
        try:
            subscription = stripe.Subscription.create(
                customer=customer_id,
                items=[{"price": price_id}],
                metadata={"tier": tier}
            )
            return {
                "subscription_id": subscription.id,
                "status": subscription.status,
                "current_period_end": subscription.current_period_end
            }
        except stripe.error.StripeError as e:
            raise Exception(f"Failed to create subscription: {str(e)}")

    @staticmethod
    async def create_payment_session(
        tier: str,
        amount: int,
        success_url: str,
        cancel_url: str
    ) -> Dict[str, Any]:
        """Create a Stripe checkout session."""
        try:
            session = stripe.checkout.Session.create(
                payment_method_types=["card"],
                line_items=[{
                    "price_data": {
                        "currency": "usd",
                        "product_data": {
                            "name": f"AI Consultant Platform - {tier.title()} Plan",
                            "description": f"Monthly subscription to {tier} tier"
                        },
                        "unit_amount": amount * 100,  # Convert to cents
                        "recurring": {"interval": "month"}
                    },
                    "quantity": 1
                }],
                mode="subscription",
                success_url=success_url,
                cancel_url=cancel_url
            )
            return {
                "session_id": session.id,
                "url": session.url
            }
        except stripe.error.StripeError as e:
            raise Exception(f"Failed to create checkout session: {str(e)}")

    @staticmethod
    async def create_one_time_payment(
        session_id: str,
        amount: int,
        consultant_mode: str
    ) -> Dict[str, Any]:
        """Create a one-time payment for a single session."""
        try:
            payment_intent = stripe.PaymentIntent.create(
                amount=amount * 100,  # Convert to cents
                currency="usd",
                metadata={
                    "session_id": session_id,
                    "consultant_mode": consultant_mode,
                    "type": "one-time-session"
                }
            )
            return {
                "payment_intent_id": payment_intent.id,
                "client_secret": payment_intent.client_secret
            }
        except stripe.error.StripeError as e:
            raise Exception(f"Failed to create payment: {str(e)}")

    @staticmethod
    async def calculate_usage_cost(
        duration_minutes: float,
        consultant_mode: str
    ) -> float:
        """Calculate cost based on usage."""
        # Pricing per minute by consultant mode
        PRICING = {
            "business": 0.80,
            "design": 0.60,
            "code": 0.70,
            "legal": 1.00,
            "medical": 0.40,
            "real_estate": 0.50,
            "marketing": 0.70
        }

        rate = PRICING.get(consultant_mode, 0.50)
        return duration_minutes * rate

    @staticmethod
    def get_pricing_tiers() -> Dict[str, Any]:
        """Get all pricing tiers and options."""
        return {
            "subscriptions": {
                "starter": {
                    "price": 199,
                    "sessions": 5,
                    "overage": 49  # Price per additional session
                },
                "professional": {
                    "price": 599,
                    "sessions": 20,
                    "overage": 39
                },
                "enterprise": {
                    "price": 1499,
                    "sessions": "unlimited",
                    "overage": 0
                }
            },
            "one_time": {
                "business": 199,
                "design": 149,
                "code": 179,
                "legal": 249,
                "medical": 99,
                "real_estate": 129,
                "marketing": 179
            },
            "usage_based": {
                "rate_per_minute": "Varies by consultant mode",
                "minimum": 10  # Minimum charge in dollars
            }
        }
