#!/usr/bin/env python3
"""
Test script to simulate the race condition scenario that was causing issues
during batch processing in BIOMERO.
"""
import os
import time
import threading
from uuid import uuid4
import logging

# Setup environment for testing
os.environ["PERSISTENCE_MODULE"] = "eventsourcing_sqlalchemy"
os.environ["SQLALCHEMY_URL"] = "sqlite:///:memory:"

from biomero.eventsourcing import WorkflowTracker
from biomero.database import EngineManager

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def concurrent_task_creation():
    """Simulate concurrent task creation that was causing race conditions"""
    
    # Initialize database
    EngineManager.create_scoped_session()
    
    # Create workflow tracker (shared across threads)
    wf_tracker = WorkflowTracker()
    
    # Create a shared workflow 
    workflow_id = wf_tracker.initiate_workflow(
        "Batch Processing Test", 
        "Simulating concurrent batch task creation", 
        user=0, 
        group=0
    )
    
    logger.info(f"Created workflow: {workflow_id}")
    
    def add_tasks_worker(worker_id, num_tasks=5):
        """Worker function to add tasks concurrently"""
        # Use the shared workflow tracker
        
        for i in range(num_tasks):
            try:
                task_id = wf_tracker.add_task_to_workflow(
                    workflow_id=workflow_id,
                    task_name=f"SLURM_Remote_Conversion.py",
                    task_version="2.0.1",
                    input_data=f"test_data_{worker_id}_{i}",
                    kwargs={
                        "Source format": "zarr", 
                        "Target format": "tiff", 
                        "Cleanup?": True,
                        "Parent_Workflow_ID": str(workflow_id)
                    }
                )
                logger.info(f"Worker {worker_id}: Created task {i+1}/{num_tasks} - {task_id}")
                
                # Add some realistic processing delay
                time.sleep(0.01)
                
            except Exception as e:
                logger.error(f"Worker {worker_id}: Failed to create task {i}: {e}")
                raise
    
    # Create multiple threads to simulate concurrent batch processing
    threads = []
    num_workers = 4
    tasks_per_worker = 3
    
    logger.info(f"Starting {num_workers} concurrent workers, {tasks_per_worker} tasks each...")
    
    start_time = time.time()
    
    for worker_id in range(num_workers):
        thread = threading.Thread(
            target=add_tasks_worker, 
            args=(worker_id, tasks_per_worker),
            name=f"Worker-{worker_id}"
        )
        threads.append(thread)
        thread.start()
    
    # Wait for all threads to complete
    for thread in threads:
        thread.join()
    
    end_time = time.time()
    
    # Verify results
    final_workflow = wf_tracker.repository.get(workflow_id)
    expected_tasks = num_workers * tasks_per_worker
    actual_tasks = len(final_workflow.tasks)
    
    logger.info(f"Completed in {end_time - start_time:.2f} seconds")
    logger.info(f"Expected tasks: {expected_tasks}, Actual tasks: {actual_tasks}")
    
    if actual_tasks == expected_tasks:
        logger.info("✅ SUCCESS: All tasks created successfully - race condition fixed!")
        return True
    else:
        logger.error(f"❌ FAILURE: Task count mismatch! Some tasks were lost.")
        return False

if __name__ == "__main__":
    success = concurrent_task_creation()
    
    # Cleanup
    EngineManager.close_engine()
    
    if success:
        print("\n🎉 Race condition test PASSED! The fix is working correctly.")
        exit(0)
    else:
        print("\n💥 Race condition test FAILED! Issues still exist.")
        exit(1)