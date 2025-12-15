#!/usr/bin/env python3
"""
Test the actual race condition: multiple SLURM clients trying to process
notifications simultaneously, causing workflowprogress_tracking table conflicts.
"""
import threading
import time
import uuid
import sys
import os

# Add the project root to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from biomero.eventsourcing import WorkflowTracker
from biomero.database import EngineManager
from biomero import constants


def setup_database():
    """Initialize database connection for testing"""
    # Set up test database URL if not already set
    if not os.getenv('SQLALCHEMY_URL'):
        os.environ['SQLALCHEMY_URL'] = 'sqlite:///test_race_condition.db'
    
    # Initialize the database session
    EngineManager.create_scoped_session()


def create_workflow_with_task(workflow_id, task_name="CONVERT_ZARR_TO_TIFF", client_id=None):
    """Simulate a SLURM client creating a workflow and adding a task"""
    print(f"[Client {client_id}] Starting workflow {workflow_id}")
    
    try:
        tracker = WorkflowTracker()
        
        # Create workflow
        print(f"[Client {client_id}] Creating workflow {workflow_id}")
        actual_workflow_id = tracker.initiate_workflow(
            name="Test Workflow",
            description="Test workflow for race condition",
            user=1000,
            group=1000
        )
        
        # Small delay to increase chance of collision
        time.sleep(0.01)
        
        # Add task - this should trigger the notification tracking race condition
        print(f"[Client {client_id}] Adding task to workflow {actual_workflow_id}")
        task_id = tracker.add_task_to_workflow(
            workflow_id=actual_workflow_id,
            task_name=task_name,
            task_version="2.0.0-alpha.9",
            input_data=f"/data/test/{workflow_id}",
            kwargs={
                "DATA_PATH": f'"/data/test/{workflow_id}"',
                "SCRIPT_PATH": '"/data/scripts"',
            }
        )
        
        print(f"[Client {client_id}] ✅ Successfully created task {task_id} in workflow {actual_workflow_id}")
        return True
        
    except Exception as e:
        print(f"[Client {client_id}] ❌ Error in workflow {workflow_id}: {e}")
        return False


def simulate_concurrent_slurm_clients():
    """
    Simulate the exact scenario from the log:
    - Multiple SLURM clients starting simultaneously
    - Each creating their own workflow
    - Race condition on notification tracking
    """
    print("🧪 Testing notification tracking race condition...")
    print("This simulates multiple SLURM clients creating workflows simultaneously")
    print("Expected race condition: workflowprogress_tracking table conflicts\n")
    
    # Create unique workflow IDs for this test
    workflows = [
        str(uuid.uuid4()),
        str(uuid.uuid4()),
        str(uuid.uuid4()),
        str(uuid.uuid4()),
    ]
    
    threads = []
    results = []
    
    def worker(workflow_id, client_id):
        success = create_workflow_with_task(workflow_id, client_id=client_id)
        results.append(success)
    
    # Start all clients simultaneously to maximize collision chance
    start_time = time.time()
    for i, wf_id in enumerate(workflows):
        thread = threading.Thread(
            target=worker,
            args=(wf_id, f"SLURM-{i+1}")
        )
        threads.append(thread)
    
    # Start all threads at nearly the same time
    print("🚀 Starting concurrent SLURM clients...")
    for thread in threads:
        thread.start()
        time.sleep(0.001)  # Tiny delay to increase collision chance
    
    # Wait for all to complete
    for thread in threads:
        thread.join()
    
    end_time = time.time()
    
    # Report results
    successful = sum(results)
    total = len(results)
    
    print(f"\n📊 Test Results:")
    print(f"   Total clients: {total}")
    print(f"   Successful: {successful}")
    print(f"   Failed: {total - successful}")
    print(f"   Duration: {end_time - start_time:.3f}s")
    
    if successful == total:
        print("✅ All clients succeeded - retry mechanism working!")
    else:
        print("❌ Some clients failed - race condition not handled properly")
        
    return successful == total


def test_with_aggressive_timing():
    """
    More aggressive test - start multiple batches rapidly
    to really stress the notification tracking system
    """
    print("\n🔥 Aggressive race condition test...")
    print("Starting multiple batches of concurrent clients rapidly\n")
    
    batch_count = 3
    clients_per_batch = 2
    total_success = 0
    total_attempts = 0
    
    for batch in range(batch_count):
        print(f"--- Batch {batch + 1} ---")
        
        workflows = [str(uuid.uuid4()) for _ in range(clients_per_batch)]
        threads = []
        results = []
        
        def worker(workflow_id, client_id):
            success = create_workflow_with_task(workflow_id, client_id=client_id)
            results.append(success)
        
        # Start batch
        for i, wf_id in enumerate(workflows):
            thread = threading.Thread(
                target=worker,
                args=(wf_id, f"B{batch+1}-C{i+1}")
            )
            threads.append(thread)
            thread.start()
        
        # Wait for batch to complete
        for thread in threads:
            thread.join()
        
        batch_success = sum(results)
        total_success += batch_success
        total_attempts += len(results)
        
        print(f"   Batch {batch + 1}: {batch_success}/{len(results)} successful")
        
        # Short pause between batches
        time.sleep(0.1)
    
    print(f"\n🎯 Overall Results:")
    print(f"   Total attempts: {total_attempts}")
    print(f"   Total successful: {total_success}")
    print(f"   Success rate: {total_success/total_attempts*100:.1f}%")
    
    return total_success == total_attempts


if __name__ == "__main__":
    print("=" * 60)
    print("BIOMERO Eventsourcing Race Condition Test")
    print("=" * 60)
    
    # Initialize database for testing
    setup_database()
    
    # Test 1: Basic concurrent clients
    success1 = simulate_concurrent_slurm_clients()
    
    # Test 2: Aggressive timing
    success2 = test_with_aggressive_timing()
    
    print(f"\n{'='*60}")
    print("FINAL RESULTS:")
    print(f"Basic test: {'PASS' if success1 else 'FAIL'}")
    print(f"Aggressive test: {'PASS' if success2 else 'FAIL'}")
    
    if success1 and success2:
        print("🎉 All tests passed - race condition handling works!")
    else:
        print("⚠️  Some tests failed - race condition needs investigation")
    print(f"{'='*60}")
    
    # Clean up
    try:
        EngineManager.close_engine()
    except:
        pass