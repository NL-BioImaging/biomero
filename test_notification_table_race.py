#!/usr/bin/env python3
"""
Test the specific notification tracking race condition that was observed in logs:
(psycopg2.errors.UniqueViolation) duplicate key value violates unique constraint "workflowprogress_tracking_pkey"
DETAIL:  Key (application_name, notification_id)=(WorkflowTracker, 1177) already exists.
"""
import threading
import time
import uuid
import sys
import os
import random

# Add the project root to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from biomero.eventsourcing import WorkflowTracker
from biomero.database import EngineManager


def setup_database():
    """Initialize database connection for testing"""
    if not os.getenv('SQLALCHEMY_URL'):
        # Use PostgreSQL for more realistic concurrency testing
        # Fall back to SQLite if PG not available
        postgres_url = "postgresql://test:test@localhost:5432/test_biomero"
        sqlite_url = "sqlite:///test_notification_race.db"
        
        try:
            # Try PostgreSQL first for better concurrency simulation
            os.environ['SQLALCHEMY_URL'] = postgres_url
            EngineManager.create_scoped_session()
            print("🗄️ Using PostgreSQL for realistic concurrency testing")
        except Exception as e:
            print(f"⚠️ PostgreSQL not available ({e}), falling back to SQLite")
            os.environ['SQLALCHEMY_URL'] = sqlite_url
            EngineManager.create_scoped_session()
    else:
        EngineManager.create_scoped_session()


def rapid_fire_workflow_creation(client_id, num_workflows=10):
    """
    Create multiple workflows rapidly from a single client
    to stress the notification tracking system
    """
    successes = 0
    failures = 0
    
    for i in range(num_workflows):
        try:
            tracker = WorkflowTracker()
            
            # Create workflow and task in quick succession
            workflow_id = tracker.initiate_workflow(
                name=f"RapidFire-{client_id}-{i}",
                description="Stress test workflow",
                user=client_id,
                group=1000
            )
            
            # No delay - create task immediately
            task_id = tracker.add_task_to_workflow(
                workflow_id=workflow_id,
                task_name="STRESS_TEST",
                task_version="1.0.0",
                input_data=f"/test/data/{workflow_id}",
                kwargs={"TEST": "true"}
            )
            
            successes += 1
            print(f"[Client {client_id}] ✅ Workflow {i+1}/{num_workflows}: {workflow_id[:8]}")
            
        except Exception as e:
            failures += 1
            print(f"[Client {client_id}] ❌ Workflow {i+1}/{num_workflows} failed: {type(e).__name__}: {e}")
            
        # Minimal delay to increase collision probability
        time.sleep(0.001)
    
    return successes, failures


def stress_test_notification_system():
    """
    Stress test the notification tracking system with multiple clients
    creating many workflows rapidly
    """
    print("🔥 Stress testing notification tracking system...")
    print("Multiple clients creating workflows rapidly to force notification ID collisions\n")
    
    num_clients = 6
    workflows_per_client = 8
    
    threads = []
    results = []
    
    def worker(client_id):
        success_count, failure_count = rapid_fire_workflow_creation(
            client_id=client_id,
            num_workflows=workflows_per_client
        )
        results.append((success_count, failure_count))
    
    print(f"🚀 Starting {num_clients} concurrent clients...")
    print(f"Each client will create {workflows_per_client} workflows rapidly\n")
    
    start_time = time.time()
    
    # Start all clients simultaneously
    for client_id in range(1, num_clients + 1):
        thread = threading.Thread(target=worker, args=(client_id,))
        threads.append(thread)
        thread.start()
    
    # Wait for all to complete
    for thread in threads:
        thread.join()
    
    end_time = time.time()
    
    # Calculate totals
    total_success = sum(result[0] for result in results)
    total_failure = sum(result[1] for result in results)
    total_attempts = total_success + total_failure
    
    print(f"\n📊 Stress Test Results:")
    print(f"   Clients: {num_clients}")
    print(f"   Workflows per client: {workflows_per_client}")
    print(f"   Total attempts: {total_attempts}")
    print(f"   Successful: {total_success}")
    print(f"   Failed: {total_failure}")
    print(f"   Success rate: {total_success/total_attempts*100:.1f}%")
    print(f"   Duration: {end_time - start_time:.3f}s")
    print(f"   Throughput: {total_attempts/(end_time - start_time):.1f} workflows/sec")
    
    # Individual client results
    print(f"\n📈 Per-client results:")
    for i, (success, failure) in enumerate(results, 1):
        print(f"   Client {i}: {success}/{success+failure} successful")
    
    if total_failure == 0:
        print("\n✅ Perfect! No failures - retry mechanism handled all conflicts")
        return True
    elif total_failure < total_attempts * 0.1:  # Less than 10% failure rate
        print(f"\n✅ Good! Low failure rate ({total_failure/total_attempts*100:.1f}%) - retry mechanism mostly working")
        return True
    else:
        print(f"\n❌ High failure rate ({total_failure/total_attempts*100:.1f}%) - retry mechanism needs improvement")
        return False


def sequential_baseline_test():
    """
    Run a sequential baseline test to ensure the system works correctly
    without concurrency stress
    """
    print("📏 Running sequential baseline test...")
    
    tracker = WorkflowTracker()
    num_workflows = 20
    
    successes = 0
    start_time = time.time()
    
    for i in range(num_workflows):
        try:
            workflow_id = tracker.initiate_workflow(
                name=f"Sequential-{i}",
                description="Sequential test workflow",
                user=9999,
                group=1000
            )
            
            task_id = tracker.add_task_to_workflow(
                workflow_id=workflow_id,
                task_name="SEQUENTIAL_TEST",
                task_version="1.0.0",
                input_data=f"/test/sequential/{i}",
                kwargs={"INDEX": str(i)}
            )
            
            successes += 1
            
        except Exception as e:
            print(f"❌ Sequential workflow {i} failed: {e}")
    
    end_time = time.time()
    
    print(f"   Sequential: {successes}/{num_workflows} successful")
    print(f"   Duration: {end_time - start_time:.3f}s")
    print(f"   Throughput: {successes/(end_time - start_time):.1f} workflows/sec")
    
    return successes == num_workflows


if __name__ == "__main__":
    print("=" * 70)
    print("BIOMERO Notification Tracking Race Condition Stress Test")
    print("=" * 70)
    
    # Initialize database
    setup_database()
    
    # Test 1: Sequential baseline
    print("\n" + "=" * 50)
    baseline_pass = sequential_baseline_test()
    
    # Test 2: Stress test
    print("\n" + "=" * 50)
    stress_pass = stress_test_notification_system()
    
    # Final results
    print(f"\n{'='*70}")
    print("FINAL RESULTS:")
    print(f"Sequential baseline: {'PASS' if baseline_pass else 'FAIL'}")
    print(f"Concurrency stress test: {'PASS' if stress_pass else 'FAIL'}")
    
    if baseline_pass and stress_pass:
        print("\n🎉 All tests passed! Retry mechanism is handling notification tracking race conditions properly!")
    elif baseline_pass and not stress_pass:
        print("\n⚠️ Baseline works but stress test shows issues under high concurrency")
    else:
        print("\n❌ Tests failed - fundamental issues detected")
    
    print(f"{'='*70}")
    
    # Clean up
    try:
        EngineManager.close_engine()
        # Clean up test files
        for filename in ['test_notification_race.db', 'test_notification_race.db-journal']:
            if os.path.exists(filename):
                os.remove(filename)
    except:
        pass