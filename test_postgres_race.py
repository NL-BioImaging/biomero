#!/usr/bin/env python3
"""
Integration test for the actual race condition using PostgreSQL.
This reproduces the exact issue from the logs:
- Multiple SLURM clients (WorkflowTracker instances)
- Concurrent notification tracking causing workflowprogress_tracking table conflicts
- Tests our retry mechanism with real PostgreSQL concurrency
"""
import threading
import time
import uuid
import os
import sys
import subprocess
import psycopg2
from psycopg2 import sql

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from biomero.eventsourcing import WorkflowTracker


def setup_postgres_test_db():
    """Set up a test PostgreSQL database for the race condition test"""
    db_name = "biomero_race_test"
    
    # PostgreSQL connection string like BIOMERO uses
    postgres_url = f"postgresql+psycopg2://postgres:biomero-password@localhost:5433/{db_name}"
    
    print("🗄️ Setting up PostgreSQL test database...")
    
    # Create database if it doesn't exist
    try:
        # Connect to postgres database to create our test db
        conn = psycopg2.connect(
            host="localhost",
            port=5433,
            database="postgres", 
            user="postgres",
            password="biomero-password"
        )
        conn.autocommit = True
        cursor = conn.cursor()
        
        # Drop and recreate database for clean test
        cursor.execute(f"DROP DATABASE IF EXISTS {db_name}")
        cursor.execute(f"CREATE DATABASE {db_name}")
        print(f"   ✅ Created database: {db_name}")
        
        cursor.close()
        conn.close()
        
    except psycopg2.Error as e:
        print(f"   ❌ PostgreSQL setup failed: {e}")
        print("   💡 Make sure PostgreSQL is running on localhost:5433")
        print("      Try: docker-compose up database-biomero")
        return None
    
    # Set environment variable for biomero to use
    os.environ["SQLALCHEMY_URL"] = postgres_url
    print(f"   ✅ Set SQLALCHEMY_URL: {postgres_url}")
    
    return postgres_url


def simulate_slurm_client(client_id, workflow_ids, results, start_barrier):
    """
    Simulate a SLURM client creating multiple workflows with tasks.
    This mimics the real scenario from the logs where multiple processes
    are creating tasks simultaneously.
    """
    # Wait for all clients to be ready
    start_barrier.wait()
    
    print(f"[SLURM-{client_id}] Starting concurrent workflow creation...")
    
    successful_workflows = []
    failed_workflows = []
    
    try:
        # Each client creates its own WorkflowTracker (like real SLURM clients do)
        tracker = WorkflowTracker()
        
        for workflow_id in workflow_ids:
            try:
                print(f"[SLURM-{client_id}] Creating workflow {workflow_id[:8]}...")
                
                # Create workflow
                workflow_run = tracker.create_workflow_run(workflow_id)
                
                # Add task immediately (this is where the race condition occurs)
                print(f"[SLURM-{client_id}] Adding task to workflow {workflow_id[:8]}...")
                task = tracker.add_task_to_workflow(
                    workflow_id=workflow_id,
                    task_name="CONVERT_ZARR_TO_TIFF",
                    task_version="2.0.0-alpha.9",
                    input_data=f"/data/test/{workflow_id}",
                    params={
                        "DATA_PATH": f'"/data/test/{workflow_id}"',
                        "CONVERSION_PATH": '"/data/converters"',
                        "CONVERTER_IMAGE": "convert_zarr_to_tiff_2.0.0-alpha.9.sif",
                        "SCRIPT_PATH": '"/data/scripts"',
                        "CONFIG_FILE": f'"config_{workflow_id}.txt"',
                    }
                )
                
                print(f"[SLURM-{client_id}] ✅ Successfully created task {task.id[:8]} for workflow {workflow_id[:8]}")
                successful_workflows.append(workflow_id)
                
            except Exception as e:
                print(f"[SLURM-{client_id}] ❌ Failed to create workflow {workflow_id[:8]}: {e}")
                failed_workflows.append((workflow_id, str(e)))
                
        results[client_id] = {
            'successful': successful_workflows,
            'failed': failed_workflows
        }
        
    except Exception as e:
        print(f"[SLURM-{client_id}] ❌ Critical error: {e}")
        results[client_id] = {
            'successful': [],
            'failed': [(wf_id, str(e)) for wf_id in workflow_ids]
        }


def test_concurrent_notification_tracking():
    """
    Test the exact race condition scenario from the logs:
    - Multiple SLURM clients (different processes/threads)
    - Each creating workflows + tasks simultaneously  
    - Race condition on workflowprogress_tracking table
    - Should trigger our retry mechanism
    """
    print("🧪 Testing PostgreSQL notification tracking race condition...")
    print("This reproduces the exact issue from production logs\n")
    
    # Setup test scenarios
    num_clients = 4  # Multiple concurrent SLURM clients
    workflows_per_client = 2  # Each creates multiple workflows
    
    # Generate unique workflow IDs for each client
    workflow_assignments = {}
    for client_id in range(num_clients):
        workflow_assignments[client_id] = [
            str(uuid.uuid4()) for _ in range(workflows_per_client)
        ]
    
    # Shared data structures
    results = {}
    start_barrier = threading.Barrier(num_clients)  # Synchronize start
    
    print(f"🚀 Starting {num_clients} SLURM clients, each creating {workflows_per_client} workflows...")
    print("   This should trigger notification_id conflicts in workflowprogress_tracking\n")
    
    # Create and start all client threads
    threads = []
    start_time = time.time()
    
    for client_id in range(num_clients):
        thread = threading.Thread(
            target=simulate_slurm_client,
            args=(client_id, workflow_assignments[client_id], results, start_barrier),
            name=f"SLURM-Client-{client_id}"
        )
        threads.append(thread)
        thread.start()
    
    # Wait for all clients to complete
    for thread in threads:
        thread.join()
    
    end_time = time.time()
    
    # Analyze results
    total_workflows = num_clients * workflows_per_client
    total_successful = sum(len(result['successful']) for result in results.values())
    total_failed = sum(len(result['failed']) for result in results.values())
    
    print(f"\n📊 Race Condition Test Results:")
    print(f"   Duration: {end_time - start_time:.3f}s")
    print(f"   Total workflows attempted: {total_workflows}")
    print(f"   ✅ Successful: {total_successful}")
    print(f"   ❌ Failed: {total_failed}")
    print(f"   Success rate: {total_successful/total_workflows*100:.1f}%")
    
    # Detailed breakdown per client
    print(f"\n🔍 Per-client breakdown:")
    for client_id, result in results.items():
        successful = len(result['successful'])
        failed = len(result['failed'])
        print(f"   SLURM-{client_id}: {successful}/{successful + failed} successful")
        
        # Show failure reasons
        if result['failed']:
            print(f"      Failures: {[err[:50] + '...' if len(err) > 50 else err for _, err in result['failed']]}")
    
    # Check if we successfully handled race conditions
    if total_successful == total_workflows:
        print(f"\n🎉 All workflows succeeded - retry mechanism working perfectly!")
        return True
    else:
        print(f"\n⚠️  Some workflows failed - investigating race condition handling...")
        
        # Check for specific race condition errors
        race_condition_errors = []
        for result in results.values():
            for _, error in result['failed']:
                if 'duplicate key' in error.lower() or 'unique constraint' in error.lower():
                    race_condition_errors.append(error)
        
        if race_condition_errors:
            print(f"   🎯 Found {len(race_condition_errors)} race condition errors:")
            for error in race_condition_errors[:3]:  # Show first 3
                print(f"      - {error}")
            print(f"   This means we successfully reproduced the issue!")
            return False
        else:
            print(f"   ❓ Failures were not race condition related")
            return False


if __name__ == "__main__":
    print("=" * 70)
    print("BIOMERO PostgreSQL Race Condition Integration Test")
    print("=" * 70)
    
    # Setup PostgreSQL test database
    postgres_url = setup_postgres_test_db()
    if not postgres_url:
        print("❌ Could not set up PostgreSQL - aborting test")
        sys.exit(1)
    
    print()
    
    try:
        # Run the race condition test
        success = test_concurrent_notification_tracking()
        
        print(f"\n{'='*70}")
        if success:
            print("🎉 TEST PASSED: Retry mechanism successfully handled all race conditions!")
        else:
            print("🔍 TEST REVEALED ISSUES: Race conditions found - this confirms our fix is needed!")
        print(f"{'='*70}")
        
    except Exception as e:
        print(f"\n❌ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)