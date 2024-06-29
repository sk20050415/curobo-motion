# Third Party
import torch
import numpy as np
# CuRobo
from curobo.geom.sdf.world import CollisionCheckerType
from curobo.geom.types import Cuboid, WorldConfig
from curobo.types.base import TensorDeviceType
from curobo.types.math import Pose
from curobo.types.robot import JointState, RobotConfig
from curobo.util.logger import setup_curobo_logger
from curobo.util_file import get_robot_configs_path, get_world_configs_path, join_path, load_yaml
from curobo.wrap.reacher.motion_gen import MotionGen, MotionGenConfig, MotionGenPlanConfig

import time
import math
import argparse
import threading
import csv
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D

# Import Flexiv RDK Python library
# fmt: off
import sys
sys.path.insert(0, "/home/robot/motion/flexiv_rdk/lib_py")
import flexivrdk
# fmt: on
from utility import parse_pt_states


def list2str(ls):
    ret_str = ""
    for i in ls:
        ret_str += str(i) + " "
    return ret_str


def plot_traj(trajectory, dt):
    # Third Party
    import matplotlib.pyplot as plt

    _, axs = plt.subplots(4, 1)
    q = trajectory.position.cpu().numpy()
    qd = trajectory.velocity.cpu().numpy()
    qdd = trajectory.acceleration.cpu().numpy()
    qddd = trajectory.jerk.cpu().numpy()
    timesteps = [i * dt for i in range(q.shape[0])]
    for i in range(q.shape[-1]):
        axs[0].plot(timesteps, q[:, i], label=str(i))
        axs[1].plot(timesteps, qd[:, i], label=str(i))
        axs[2].plot(timesteps, qdd[:, i], label=str(i))
        axs[3].plot(timesteps, qddd[:, i], label=str(i))

    plt.legend()
    # plt.savefig("test.png")
    plt.show()


def demo_motion_gen(begin_q=None, target_position=None, target_orientation=None):
    # Standard Library
    PLOT = False
    js = False
    tensor_args = TensorDeviceType()
    world_file = "virtual_test.yml"
    robot_file = "flexiv_plus_de.yml"
    motion_gen_config = MotionGenConfig.load_from_robot_config(
        robot_file,
        world_file,
        tensor_args,
        interpolation_dt=0.01,
    )

    motion_gen = MotionGen(motion_gen_config)

    motion_gen.warmup(enable_graph=True, warmup_js_trajopt=js)
    robot_cfg = load_yaml(join_path(get_robot_configs_path(), robot_file))["robot_cfg"]
    robot_cfg = RobotConfig.from_dict(robot_cfg, tensor_args)
    retract_cfg = motion_gen.get_retract_config()
    state = motion_gen.rollout_fn.compute_kinematics(
        JointState.from_position(retract_cfg.view(1, -1))
    )
    #if begin_q is None:
    #    begin_q = [0.0, -0.2, 0.0, 0.57, 0.0, 0.2, 0.0]
    begin_position = np.array(begin_q)
    begin_cfg = tensor_args.to_device(begin_position)
    begin_state = JointState.from_position(begin_cfg.view(1, -1))

    retract_pose = Pose(state.ee_pos_seq.squeeze(), quaternion=state.ee_quat_seq.squeeze())
    start_state = JointState.from_position(retract_cfg.view(1, -1))
    goal_state = start_state.clone()
    goal_state.position[..., 3] -= 0.1

    first_position = target_position
    first_orientation= target_orientation
    ee_translation_goal = first_position
    ee_orientation_teleop_goal = first_orientation
    # compute curobo solution:
    ik_goal = Pose(
        position=tensor_args.to_device(ee_translation_goal),
        quaternion=tensor_args.to_device(ee_orientation_teleop_goal),
        )
    print(tensor_args.to_device(ee_translation_goal))
    

    if js:
        result = motion_gen.plan_single_js(
            start_state,
            goal_state,
            MotionGenPlanConfig(
                max_attempts=1, enable_graph=False, enable_opt=True, enable_finetune_trajopt=True
            ),
        )
    else:
        result = motion_gen.plan_single(
            begin_state, ik_goal, MotionGenPlanConfig(max_attempts=1, enable_graph=False, enable_opt=True, enable_finetune_trajopt=True)
        )
        # print("me")
    traj = result.get_interpolated_plan()
    print("Trajectory Generated: ", result.success, result.solve_time, result.status)
    print("dt",result.interpolation_dt)
    # print("traj",traj)
    if PLOT and result.success.item():
        plot_traj(traj, result.interpolation_dt)
    return traj


def main():
    setup_curobo_logger("error")

    frequency = 100
    # frequency >= 1 and frequency <= 100
    robot_ip = "192.168.2.100"            
    local_ip = "192.168.2.104"
    robot_states = flexivrdk.RobotStates()
    log = flexivrdk.Log()
    mode = flexivrdk.Mode

    photo_pose = [0.436954, -0.171356, 0.516743, 179.042, 14.506, 179.717]
    begin_pose = [0.436954, -0.171356, 0.536743, 177.687, 0.176, 179.717]

    safe_origin = [0.475317, -0.371926, 0.462725, 177.799, -14.284, 158.034]
    pre_catch = [0.507490, -0.091361, 0.506778, -0.886, -179.512, 128.808]
    catch = [0.507490, -0.091361, 0.484320, -0.886, -179.512, 128.808]
    up = [0.512479, -0.091361, 0.536778, -0.886, -179.512, 128.808]
    move_tube = [0.470688, -0.365915, 0.536778, 4.037, -151.671, 128.565]
    place_tube = [0.470688, -0.365915, 0.451999, 4.037, -151.671, 128.565] 
    

    try:
        robot = flexivrdk.Robot(robot_ip, local_ip)
        gripper = flexivrdk.Gripper(robot)

        period = 1.0 / frequency
        loop_time = 0
        place_time = 0
        move_time = 0
        print(
            "Sending command to robot at",
            frequency,
            "Hz, or",
            period,
            "seconds interval",
        )
        # Clear fault on robot server if any
        if robot.isFault():
            log.warn("Fault occurred on robot server, trying to clear ...")
            robot.clearFault()
            time.sleep(2)
            if robot.isFault():
                log.error("Fault cannot be cleared, exiting ...")
                return
            log.info("Fault on robot server is cleared")

        # Enable the robot, make sure the E-stop is released before enabling
        log.info("Enabling robot ...")
        robot.enable()

        while not robot.isOperational():
            time.sleep(1)
        log.info("Robot is now operational")

        while robot.isBusy():
            time.sleep(1)
            

        # safe origin
        robot.setMode(mode.NRT_PRIMITIVE_EXECUTION)
        robot.executePrimitive("MoveJ(target=-10.99 -10.53 -21.30 71.14 6.07 -8.95 -8.99, relative=false)") 
        while parse_pt_states(robot.getPrimitiveStates(), "reachedTarget") != "1":
            time.sleep(1)
        log.info("MoveL:photo")
        robot.executePrimitive(
            f"MoveL(target={list2str(photo_pose)} WORLD WORLD_ORIGIN, maxVel=0.1)"
        )
        while parse_pt_states(robot.getPrimitiveStates(), "reachedTarget") != "1":
            time.sleep(1)
        time.sleep(7)


        log.info("MoveL:begin_origin")
        robot.executePrimitive(
            f"MoveL(target={list2str(begin_pose)} WORLD WORLD_ORIGIN, maxVel=0.07)"
        )
        while parse_pt_states(robot.getPrimitiveStates(), "reachedTarget") != "1":
            time.sleep(1)
        
        gripper.move(0.015, 0.003, 30)

        log.info("MoveL:pre_catch")
        robot.executePrimitive(
            f"MoveL(target={list2str(pre_catch)} WORLD WORLD_ORIGIN, maxVel=0.25)"
        )
        while parse_pt_states(robot.getPrimitiveStates(), "reachedTarget") != "1":
            time.sleep(1)
        
        robot.setMode(mode.NRT_PRIMITIVE_EXECUTION)
        log.info("MoveL:catch")
        robot.executePrimitive(
            f"MoveL(target={list2str(catch)} WORLD WORLD_ORIGIN, maxVel=0.06)"
        )
        while parse_pt_states(robot.getPrimitiveStates(), "reachedTarget") != "1":
            time.sleep(1)
            
        gripper.move(0.0085, 0.07, 50)
        time.sleep(2)
        robot.getRobotStates(robot_states)
        print("catch",robot_states.tcpPoseDes)
        q2 = robot_states.q
        print("catchq",q2)
        
        log.info("MoveL:up")
        robot.executePrimitive(
            f"MoveL(target={list2str(up)} WORLD WORLD_ORIGIN, maxVel=0.07)"
        )
        while parse_pt_states(robot.getPrimitiveStates(), "reachedTarget") != "1":
            time.sleep(1)
        robot.getRobotStates(robot_states)
        print("up",robot_states.q)

        robot.setMode(mode.NRT_JOINT_POSITION)
        robot.getRobotStates(robot_states)
        init_pos = robot_states.q.copy()
        print("Initial positions set to: ", init_pos)

        DOF = len(robot_states.q)
        print("robot_states.q: ", robot_states.q)
        # Initialize target vectors
        target_pos = init_pos.copy()
        target_vel = [0.0] * DOF
        target_acc = [0.0] * DOF
        # Joint motion constraints
        MAX_VEL = [0.8] * DOF
        MAX_ACC = [1.0] * DOF
        q4 = [-0.04931390285, -0.0213746093, 0.0988878086, 1.440139294, -0.019082969, -0.112457253, -2.2027254]
        fou_pos = q4
        fou_place=np.array([0.470688, -0.365915, 0.536778])
        fou_orient=np.array([0.07535227, 0.8767513633, -0.4127150476, 0.235149771])
        move_traj = demo_motion_gen(begin_q = fou_pos, target_position=fou_place, target_orientation=fou_orient)
        move_art_pos = move_traj.position.cpu().numpy()
        move_loop_counter = len(move_art_pos)
        move_count = 0
        print("move_pos",len(move_art_pos))
        
        while (move_time <= move_loop_counter * period):
            time.sleep(period)

            if robot.isFault():
                raise Exception("Fault occurred on robot server, exiting ...")
          
            for i in range(DOF):
                target_pos[i] = move_art_pos[move_count][i]
            # Send command
            robot.sendJointPosition(
                target_pos, target_vel, target_acc, MAX_VEL, MAX_ACC
            )
            move_count += 1
            move_time += period
        time.sleep(3.5)

        robot.setMode(mode.NRT_PRIMITIVE_EXECUTION)
        log.info("MoveL:place tube")
        robot.executePrimitive(
            f"MoveL(target={list2str(place_tube)} WORLD WORLD_ORIGIN, maxVel=0.09)"
        )
        while parse_pt_states(robot.getPrimitiveStates(), "reachedTarget") != "1":
            time.sleep(1)
        robot.getRobotStates(robot_states)
        print("placedes",robot_states.tcpPoseDes)

        monitoring = False
      
        log.info("Opening gripper")
        gripper.move(0.015, 0.1, 20)
        time.sleep(1.5)

        robot.stop()

    except Exception as e:
        log.error(str(e))

if __name__ == "__main__":
    main()