// A Moore state machine: an enum state register, next-state and output logic.

module traffic_light (
    input  logic clk,
    input  logic rst_n,
    input  logic car_waiting,
    output logic red,
    output logic yellow,
    output logic green
);
  typedef enum logic [1:0] {
    RED,
    GREEN,
    YELLOW
  } state_t;

  state_t state, next_state;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) state <= RED;
    else state <= next_state;
  end

  always_comb begin
    case (state)
      RED:     next_state = car_waiting ? GREEN : RED;
      GREEN:   next_state = YELLOW;
      default: next_state = RED;
    endcase
  end

  assign red    = state == RED;
  assign yellow = state == YELLOW;
  assign green  = state == GREEN;
endmodule
